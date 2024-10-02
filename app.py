import os

from dotenv import load_dotenv
import mariadb
import json
from datetime import datetime
from datetime import date
import websockets
from websockets.exceptions import ConnectionClosed
import logging
import requests
import asyncio
from threading import Event
from steam_web_api import Steam

if os.path.exists(".env"):
    load_dotenv()

ALL_ACTIONS_TO_MONITOR = ['CONNECTED']

class CustomDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.try_datetime, *args, **kwargs)

    @staticmethod
    def try_datetime(d):
        ret = {}
        for key, value in d.items():
            try:
                ret[key] = datetime.fromisoformat(value)
            except (ValueError, TypeError):
                ret[key] = value
        return ret


def need_profile_check(steamid):
    with DBConnection() as cur:
        cur.execute(
            "SELECT check_complete, last_checked FROM usertable WHERE steamid = ?",
            [steamid])
        result = cur.fetchall()
        if not result:
            # Don't know account, check
            return True
        if result[0][0] == True:
            # Account was already checked and is valid, don't check
            return False
        today = date.today()
        date_checked = result[0][1]
        difference_in_days = (today - date_checked).days
        if difference_in_days < int(os.environ["check_profiles_every_days"]):
            # If already checked in last X days, don't check
            return False
        else:
            # Was not checked in last three days and is not valid, so check
            return True



class SteamAPI:
    def __init__(self):
        KEY = os.environ["Steam_WebAPI-Key"]
        self.steam = Steam(KEY)

    def getprofile(self, steamid):
        user = self.steam.users.get_user_details(steamid)
        return user["player"]
    def getownedgames(self, steamid):
        games = self.steam.users.get_owned_games(steamid, include_appinfo=True, includ_free_games=False)
        return games

def check_account_own_hll(games):
    for game in games["games"]:
        if game["appid"] == int(os.environ["hll_appid"]):
            return True
    return False

def add_player_to_db(steamid, check_successfull):
    steamid = int(steamid)
    check_successfull = bool(check_successfull)
    with DBConnection() as cur:
        cur.execute(
            "INSERT INTO usertable (steamid, check_complete, last_checked) VALUES(?, ?, CURDATE()) ON DUPLICATE KEY UPDATE check_complete=?, last_checked=CURDATE()",
            [steamid, check_successfull, check_successfull])


def check_player(steamid):
    logging.info("Checking id " + steamid)
    steamapi = SteamAPI()
    profile = steamapi.getprofile(steamid)
    if bool(os.environ["Ban_player_if_communityprofile_not_configured"]) == True and profile["profilestate"] != 1:
        logging.info("Banning ID because profile not configured")
        Serverrequest.add_blacklist_record(steamid, os.environ["No_Communityprofile_Banreason"])
    if profile["communityvisibilitystate"] != 3:
        logging.info("Profile private")
        add_player_to_db(steamid, False)
        return
    current_date = datetime.now()
    timecreated = datetime.fromtimestamp(profile["timecreated"])
    difference_in_days = (current_date - timecreated).days
    if difference_in_days < int(os.environ["minimal_account_age_days"]):
        logging.info("Banning ID because account too young")
        Serverrequest.add_blacklist_record(steamid, os.environ["minimal_account_age_banreason"])
        return
    if bool(os.environ["check_if_player_owns_hll"]) == True:
        games = steamapi.getownedgames(steamid)
        if games:
            if check_account_own_hll(games) == False:
                logging.info("Banning Player don't own HLL")
                Serverrequest.add_blacklist_record(steamid, os.environ["player_dont_own_hll_banreason"])
                return
        else:
            # Can't see games, so can't check
            if bool(os.environ["check_player_regurarly_if_games_not_public"]):
                add_player_to_db(steamid, False)
            else:
                add_player_to_db(steamid, True)
            return
    logging.info("Player seems ok")
    add_player_to_db(steamid, True)


# ----------------------------CLASS CRCONWebSocket--------------------------------------------------------------
# The class CRCONWebSocketClient provides init and control fuctions for the websocket connected to the HLL-Server
# ---------------------------------------------------------------------------------------------------------------
class CRCONWebSocketClient:

    def __init__(self, server, ):
        self.server = server
        self.stop_event = None

    async def start_socket(self, stop_event):
        self.stop_event = stop_event

        headers = {"Authorization": f"Bearer {self.server.rcon_api_key}"}
        if self.server.rcon_login_headers:
            headers.update(self.server.rcon_login_headers)
        websocket_url = self.server.rcon_web_socket + "/ws/logs"
        while not self.stop_event.is_set():
            try:
                async with websockets.connect(websocket_url, extra_headers=headers,
                                              max_size=1_000_000_000) as websocket:
                    logging.info(f"Connecting to {websocket_url}")
                    try:
                        await websocket.send(json.dumps({"last_seen_id": None, "actions": ALL_ACTIONS_TO_MONITOR}))
                    except ConnectionClosed:
                        logging.warning(
                            f"ConnectionClosed exception",
                            exc_info=cc)
                        break
                    logging.info(f"Connected to CRCON websocket {websocket_url}")

                    while not self.stop_event.is_set():
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            await self.handle_incoming_message(websocket, message)

                        except asyncio.TimeoutError:
                            logging.debug("timeout error")
                        except asyncio.exceptions.CancelledError:
                            logging.debug("cancelled error")
                        except ConnectionClosed as cc:
                            logging.warning(
                                f"ConnectionClosed exception",
                                exc_info=cc)
                            break

                        except Exception as e:
                            logging.error(f"Error during handling of message by {message}", exc_info=e)

            except Exception as e:
                logging.error(f"Error connecting to {websocket_url}", exc_info=e)
            await asyncio.sleep(5)

    async def handle_incoming_message(self, websocket, message):
        json_object = json.loads(message, cls=CustomDecoder)
        if json_object:
            logs_bundle = json_object.get('logs', [])
            for logentry in logs_bundle:
                # Analysing the log stream for messages containing the map vote command !Map
                player = logentry['log']['player_name_1']
                steamid = logentry['log']['player_id_1']
                if not steamid.isdigit():
                    # No SteamID
                    return
                if not need_profile_check(steamid):
                    # Already checked, no need to query steam
                    return
                profile = Serverrequest.get_player_profile(steamid)
                if profile["flags"]:
                    for flag in profile["flags"]:
                        if flag["flag"] == os.environ["Whitelist_Flag"]:
                            # Player is whitelisted
                            return
                check_player(steamid)



            # reset all flags
            player = None
            steamid = None
            message_Chat = None
            action = None


#----------------------------CLASS Server---------------------------------------------------------------
#The class Server provides the initialisation function for the serverobjekt used by the websocket
#--------------------------------------------------------------------------------------------------------
class Server:
    def __init__(self, rcon_web_socket, rcon_api_key ):
        self.rcon_login_headers = None
        self.rcon_web_socket = rcon_web_socket
        self.rcon_api_key = rcon_api_key
    def stop_event(self):
        print("Socket stopped")


# ----------------------------CLASS SERVERREQUEST---------------------------------------------------------
# The class Serverrequest provides functions to interact with the HLL server via HTTP post/get
# --------------------------------------------------------------------------------------------------------
class Serverrequest:

    def get_player_profile(steam_id):
        headers = {
            "Authorization": f"Bearer {os.environ["Server_Api_Key"]}",
            "Connection": "keep-alive",
            "Content-Type": "application/json"
        }
        data_request = {
            "player_id": steam_id
        }
        request_url = f"https://{os.environ["Server_URL"]}/api/get_player_profile"
        response = requests.get(request_url, params=data_request, headers=headers)
        data = response.json()
        return data["result"]

    def add_blacklist_record(steam_id, reason):
        headers = {
            "Authorization": f"Bearer {os.environ["Server_Api_Key"]}",
            "Connection": "keep-alive",
            "Content-Type": "application/json"
        }
        data_request={
            "blacklist_id": os.environ["BlacklistID"],
            "player_id": steam_id,
            "reason": reason,
            "admin_name": os.environ["Admin-Name"]
        }
        request_url = f"https://{os.environ["Server_URL"]}/api/add_blacklist_record"
        response = requests.post(request_url, data=json.dumps(data_request), headers=headers)



#----------------------------CLASS DB--------------------------------------------------------------
#Handles DB-Connection
#---------------------------------------------------------------------------------------------------------------
class DBConnection:
    def __init__(self):
        self.user = os.environ["DB_User"]
        self.password = os.environ["DB_Password"]
        self.host = os.environ["DB_Host"]
        self.database = os.environ["DB_Database"]
        self.port = int(os.environ["DB_Port"])

    def __enter__(self):
        self.conn = mariadb.connect(user=self.user, password=self.password,
                                    host=self.host, database=self.database, port=self.port)
        self.cursor = self.conn.cursor()
        return self.cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.commit()
        self.cursor.close()
        self.conn.close()

if __name__ == "__main__":
    #Events
    stopevent = Event()

    #Client-Server setup

    ServerUrl ="wss://"+ os.environ["Server_URL"]
    Serverhandle = Server(ServerUrl,os.environ["Server_Api_Key"])
    Client = CRCONWebSocketClient(Serverhandle)

    #Client start
    asyncio.run(CRCONWebSocketClient.start_socket(Client, stopevent))
