#!/bin/sh
# Example hook. A real pack's hook would, say, create a test user or set a feature flag.
# It gets its arguments as one JSON text in $1, and JOURNEYS_APP / JOURNEYS_SERIAL in the env.
echo "note from $JOURNEYS_APP: $1"
