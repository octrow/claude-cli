# README demo: one real call through OmniRoute, recorded with vhs.
# Re-record from the repo root: vhs assets/demo/demo.tape
# Needs omniroute + a logged-in claude; it spends one haiku call.

Output assets/readme/demo.gif

Set Shell bash
Set FontFamily "DejaVu Sans Mono"
Set FontSize 18
Set Width 1140
Set Height 250
Set Padding 28
Set TypingSpeed 25ms
Set Theme { "name": "hero-dark", "background": "#121517", "foreground": "#E6E8EA", "cursor": "#5CC6B6", "selection": "#2F373B", "black": "#1A1F22", "red": "#F2A066", "green": "#5CC6B6", "yellow": "#F2A066", "blue": "#7FB2E5", "magenta": "#C79BE0", "cyan": "#5CC6B6", "white": "#E6E8EA", "brightBlack": "#A1A9B0", "brightRed": "#F2A066", "brightGreen": "#5CC6B6", "brightYellow": "#F2A066", "brightBlue": "#7FB2E5", "brightMagenta": "#C79BE0", "brightCyan": "#5CC6B6", "brightWhite": "#FFFFFF" }

Hide
Type "uv run -q python" Enter
Sleep 2s
Ctrl+L
Show

Type "from claude_cli import run_claude, extract_json" Enter
Sleep 400ms
Type `res = run_claude('Tag "CI is red" as JSON {"type": str, "urgent": bool}', model="haiku")` Enter
Wait+Line@60s />>> *$/
Sleep 300ms
Type "extract_json(res.text)" Enter
Sleep 1.5s
Type "res.duration_ms" Enter
Sleep 3s
