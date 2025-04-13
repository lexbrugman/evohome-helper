ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /opt/evohome-helper

# Copy data for add-on
COPY . ./

RUN pip3 install --no-cache-dir -r src/requirements.txt

CMD [ "./run.sh" ]
