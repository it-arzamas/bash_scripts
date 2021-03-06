#!/usr/bin/env sh

set -e

if [ $# -lt 2 ]; then
  SCRIPT_NAME=$(basename $0);
  echo "No arguments supplied. Usage: $SCRIPT_NAME <SERVICE_NAME> '<EXEC_COMMAND>' ['<WEBHOOK>']"
  echo
  echo "Example with webhook:"
  echo "    $SCRIPT_NAME httpd_nginx 'nginx -s reload' 'http://some-monitoring-service/nginx-reload-status'"
  echo
  echo "Example for usage inside host cron:"
  echo "    /usr/local/bin/$SCRIPT_NAME httpd_nginx 'nginx -s reload' >> /var/logs/docker-service-exec/docker-service-exec-hostname.log 2>&1"
  echo
  echo "This script take service name and command to run inside service's container. Webhook is optional, if set, script send HTTP POST to given url with results in json format. Deploy this script and related cron job to every swarm node, because you probably don't know what node hosted service container."
  exit 1
fi

SERVICE_NAME=$1
EXEC_COMMAND=$2
WEBHOOK=${3:-}
CONTAINER_ID=$(docker ps | grep -i "${SERVICE_NAME}" | awk '{print $1}')

if [ -n "${CONTAINER_ID}" ]
then
  echo "$(date --utc --iso-8601=seconds) [${SERVICE_NAME}] Start executing '${EXEC_COMMAND}'..."

  set +e
  OUTPUT=$(docker exec "${CONTAINER_ID}" $EXEC_COMMAND 2>&1)
  RETURN_CODE=$?
  set -e

  DATETIME=$(date --utc --iso-8601=seconds)
  OUTPUT_LC=$(echo "${OUTPUT}" | grep -c '^')
  echo "${DATETIME} [${SERVICE_NAME}] Commanf '${EXEC_COMMAND}' exited with code ${RETURN_CODE}."
  echo "${DATETIME} [${SERVICE_NAME}] Output of ${OUTPUT_LC} line(s) start:"
  echo "${OUTPUT}"
  echo "${DATETIME} [${SERVICE_NAME}] Output ends."

  if [ -n "${WEBHOOK}" ]
  then
    curl -d "{\"output\":\"${OUTPUT}\", \"return_code\":\"${RETURN_CODE}\"}" \
      -H "Content-Type: application/json" -X POST "${WEBHOOK}"
  fi

else
  echo "$(date --utc --iso-8601=seconds) [${SERVICE_NAME}] No suitable container found, exit."
fi
