FROM locustio/locust:2.47.0
USER root
WORKDIR /opt/getddo
COPY loadtest/requirements.txt /opt/getddo/loadtest/requirements.txt
RUN pip install --no-cache-dir -r /opt/getddo/loadtest/requirements.txt
COPY loadtest/*.py /opt/getddo/loadtest/
ENV PYTHONPATH=/opt/getddo PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["locust", "-f", "/opt/getddo/loadtest/locustfile.py"]
