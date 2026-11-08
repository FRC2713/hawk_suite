FROM python:3.14-alpine
RUN pip install --no-cache-dir cryptography==46.0.4
COPY provider.py /provider.py
CMD ["python", "/provider.py"]
