#!/bin/bash

FILE="$1"
POD=$(kubectl get pod -n streamedge -l app=watcher -o jsonpath='{.items[0].metadata.name}')

if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    echo "Video file not found."
    exit 1
fi

if [ -z "$POD" ]; then
    echo "Watcher pod not found."
    exit 1
fi

NAME=$(basename "$FILE")

kubectl cp "$FILE" streamedge/$POD:/app/incoming/$NAME.part &&
kubectl exec -n streamedge "$POD" -- mv "/app/incoming/$NAME.part" "/app/incoming/$NAME"
