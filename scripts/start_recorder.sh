#!/bin/bash
SYMBOL=${1:-btcusdt}
SESSION="rec_${SYMBOL}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session $SESSION exists."
    echo "  Attach: tmux attach -t $SESSION"
    echo "  Kill:   tmux kill-session -t $SESSION"
    exit 0
fi

echo "Starting recorder for $SYMBOL..."
tmux new-session -d -s "$SESSION"
tmux send-keys -t "$SESSION" "cd trading-projects/l2-mm-system && conda activate l2mm" Enter
tmux send-keys -t "$SESSION" "python -m src.recorder.simple_recorder --symbol $SYMBOL" Enter

echo "Recorder started!"
echo "  Attach: tmux attach -t $SESSION"
echo "  Detach: Ctrl+B then D"
echo "  Kill:   tmux kill-session -t $SESSION"
