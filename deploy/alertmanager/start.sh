#!/bin/sh
# Start Alertmanager with the webhook receiver when DA_AGENT_ALERT_WEBHOOK_URL is set (ADR 0044).
# The URL goes to a file only this container reads, never into the config or the logs.
set -eu
mkdir -p /tmp/alertmanager
if [ -n "${DA_AGENT_ALERT_WEBHOOK_URL:-}" ]; then
    printf '%s' "$DA_AGENT_ALERT_WEBHOOK_URL" > /tmp/alertmanager/webhook_url
    receiver=webhook
else
    receiver=none
fi
sed "s/RECEIVER/$receiver/" /etc/alertmanager/alertmanager.yml > /tmp/alertmanager/config.yml
echo "alertmanager: sending alerts to receiver '$receiver'"
# One instance: no cluster, so no gossip port that another container could join.
exec /bin/alertmanager \
    --config.file=/tmp/alertmanager/config.yml \
    --storage.path=/alertmanager \
    --web.listen-address=:9093 \
    --cluster.listen-address=
