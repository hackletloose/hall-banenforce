create table usertable
(
    steamid        bigint     not null
        primary key,
    check_complete tinyint(1) null,
    last_checked   date       null
);

create index usertable_steamid_index
    on usertable (steamid);

