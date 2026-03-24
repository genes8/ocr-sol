#!/usr/bin/env bash
# rabbitmq-reset-queues.sh
#
# Brise i redeklarise sve OCR-SaaS Celery queues kako bi RabbitMQ prihvatio
# novi x-max-priority=10 argument (Feature 5 — Priority Lane).
#
# UPOZORENJE: Poruke u queueovima ce biti izgubljene.
# Zaustavi sve Celery workere i sacekaj da se aktivni taskovi zavrse pre pokretanja.
#
# Upotreba:
#   ./scripts/rabbitmq-reset-queues.sh [rabbitmq-node]
#
# Primeri:
#   ./scripts/rabbitmq-reset-queues.sh                        # lokalni node (rabbit@localhost)
#   ./scripts/rabbitmq-reset-queues.sh rabbit@rabbitmq        # k8s / docker compose
#   RABBITMQ_NODE=rabbit@rabbitmq ./scripts/rabbitmq-reset-queues.sh

set -euo pipefail

RABBITMQ_NODE="${1:-${RABBITMQ_NODE:-rabbit@localhost}}"
VHOST="${RABBITMQ_VHOST:-/}"

QUEUES=(
  "preprocess_queue"
  "ocr_queue"
  "classification_queue"
  "structuring_queue"
  "reconciliation_queue"
  "validation_queue"
  "dead_letter_queue"
)

echo "==> RabbitMQ node: ${RABBITMQ_NODE}"
echo "==> Virtual host:  ${VHOST}"
echo ""
echo "UPOZORENJE: Ova skripta ce obrisati sledece queues:"
for q in "${QUEUES[@]}"; do
  echo "    - ${q}"
done
echo ""

# Interaktivna potvrda ako je terminal
if [ -t 0 ]; then
  read -r -p "Nastavi? [y/N] " confirm
  if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
    echo "Prekinuto."
    exit 1
  fi
fi

echo ""
echo "==> Brisanje queues..."

for q in "${QUEUES[@]}"; do
  echo -n "    Brisi '${q}'... "
  if rabbitmqctl --node "${RABBITMQ_NODE}" delete_queue "${q}" --vhost "${VHOST}" 2>/dev/null; then
    echo "OK"
  else
    echo "PRESKOCENO (queue ne postoji ili greska — nastavlja se)"
  fi
done

echo ""
echo "==> Gotovo. Pokreni Celery workere — queues ce biti automatski rekreirani"
echo "    sa x-max-priority=10 argumentom pri prvom startu."
