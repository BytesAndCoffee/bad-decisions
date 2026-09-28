# Recording the terminal demo

[`../scripts/record_demo.sh`](../scripts/record_demo.sh) records real Regret
output; it does not print a fabricated transcript. The hosted service must be
reachable and `asciinema` must be installed.

```bash
scripts/record_demo.sh build/demo.cast
```

The script records `regret --version`, `regret health`, and a draw from the
owner-authored Coffee pack. It deliberately omits installation chatter and
multiplayer secrets. Review the recording before publishing it.

To produce an SVG with `svg-term-cli`:

```bash
svg-term --in build/demo.cast --out docs/assets/regret-demo.svg \
  --window --width 92 --height 20
```

For a GIF, use `agg build/demo.cast docs/assets/regret-demo.gif`. Keep the final
asset under roughly 5 MB and 30 seconds for GitHub and social previews.
