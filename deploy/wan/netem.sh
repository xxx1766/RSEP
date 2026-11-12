#!/bin/sh
set -eu

action=${1:-apply}
: "${IFACES:?set IFACES to a space-separated interface list}"
DELAY_MS=${DELAY_MS:-32}
JITTER_MS=${JITTER_MS:-5}
LOSS_PCT=${LOSS_PCT:-0.1}
RATE=${RATE:-1Gbit}
LIMIT=${LIMIT:-100000}

for iface in $IFACES; do
    case "$action" in
        apply)
            tc qdisc replace dev "$iface" root netem \
                limit "$LIMIT" \
                delay "${DELAY_MS}ms" "${JITTER_MS}ms" \
                loss "${LOSS_PCT}%" \
                rate "$RATE"
            ;;
        remove)
            tc qdisc del dev "$iface" root 2>/dev/null || true
            ;;
        show)
            tc -s qdisc show dev "$iface"
            ;;
        *)
            echo "usage: $0 {apply|remove|show}" >&2
            exit 2
            ;;
    esac
done
