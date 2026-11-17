# Journeys

End-to-end journeys for Android apps. Journeys drives a device with
[Speedrun](https://github.com/hubastard/speedrun) and keeps each company's app map and journeys
in a private **pack**. Status: early work; see [docs/PLAN.md](docs/PLAN.md).

```sh
uv sync
uv run journeys --pack examples/settings-pack doctor
uv run journeys --pack examples/settings-pack run smoke --driver fake   # no phone needed
```

License: MIT.
