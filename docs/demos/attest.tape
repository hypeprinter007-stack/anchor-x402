# attest.tape — renders docs/demos/attest.mp4. Run from the repo root:
#
#   vhs docs/demos/attest.tape
#
# This drives examples/agent/attest-cast.mjs — a deterministic, paced replay of
# a REAL attest-decision-receipt.mjs run (the signer + Base/Solana tx links are
# genuine on-chain anchors; only the pacing is controlled so the recorder
# doesn't depend on live network latency). The cast is staged under the real
# example's name so the on-screen command matches the one the demos page tells
# people to run. To capture a fresh live run instead, point Type at
# `node attest-decision-receipt.mjs` with a funded BASE_PRIVATE_KEY exported —
# but expect 40-60s of variable anchoring latency.

Output docs/demos/attest.mp4

Require node

Set Shell "zsh"
Set FontSize 18
Set Width 1280
Set Height 880
Set Padding 28
Set Theme { "background": "#0b1220", "foreground": "#e2e8f0", "cursor": "#7dd3fc", "selection": "#1e293b", "black": "#0b1220", "red": "#f87171", "green": "#7fd17f", "yellow": "#e6c07b", "blue": "#7dd3fc", "magenta": "#c678dd", "cyan": "#56b6c2", "white": "#e2e8f0", "brightBlack": "#94a3b8", "brightRed": "#f87171", "brightGreen": "#98c379", "brightYellow": "#e5c07b", "brightBlue": "#7dd3fc", "brightMagenta": "#c678dd", "brightCyan": "#56b6c2", "brightWhite": "#ffffff" }
Set TypingSpeed 55ms

# Off-camera: stage the cast under the real example's filename in a temp dir.
Hide
Type "D=$(mktemp -d) && cp examples/agent/attest-cast.mjs $D/attest-decision-receipt.mjs && cd $D && clear"
Enter
Show

Sleep 800ms
Type "node attest-decision-receipt.mjs"
Sleep 500ms
Enter

# Deterministic cast (~8s of output); fixed sleep is reliable where Wait+Screen
# is not. Holds a final beat on the green "matches anchor ✓".
Sleep 12s
