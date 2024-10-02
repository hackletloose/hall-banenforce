FROM python:3.12
LABEL authors="HaLL Development: LordofAgents"
WORKDIR /app
ADD requirements.txt .
RUN pip install -r requirements.txt
ADD app.py .
CMD ["python","-u","/app/app.py"]
