FROM harbor.app.com/devops-infra/generic-python39:1.0.0

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

WORKDIR /
COPY /app /app

CMD ["python","-m","app.main","--host","0.0.0.0","--port","5000"]
