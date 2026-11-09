# Lambda como imagen de contenedor: Chromium no entra en un zip de Lambda.
# La version de la imagen debe coincidir con playwright== de requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /var/task
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt awslambdaric

COPY *.py ./
COPY demo ./demo

ENTRYPOINT ["python3", "-m", "awslambdaric"]
CMD ["index.lambda_handler"]