#!/bin/sh
set -eu

streamlit run gui.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true &

streamlit_pid="$!"

warn_no_x11() {
  echo "Warning: X11 browser launch is not available: $1" >&2
  echo "Streamlit is still available at http://localhost:8501 on the host." >&2
}

x11_socket_available() {
  case "${DISPLAY:-}" in
    :*)
      display_number="${DISPLAY#:}"
      display_number="${display_number%%.*}"
      [ -S "/tmp/.X11-unix/X${display_number}" ]
      ;;
    *)
      return 0
      ;;
  esac
}

if [ -z "${DISPLAY:-}" ]; then
  warn_no_x11 "DISPLAY is not set."
elif ! command -v chromium >/dev/null 2>&1; then
  warn_no_x11 "chromium is not installed in the image."
elif ! id openadsim >/dev/null 2>&1; then
  warn_no_x11 "openadsim user is not available in the image."
elif ! x11_socket_available; then
  warn_no_x11 "DISPLAY=${DISPLAY} has no matching /tmp/.X11-unix socket mounted."
else
  for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8501/_stcore/health >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  runuser -u openadsim -- chromium \
    --no-sandbox \
    --test-type \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-extensions \
    --disable-infobars \
    --no-default-browser-check \
    --disable-features=Translate \
    --app=http://127.0.0.1:8501 >/tmp/openadsim-browser.log 2>&1 &
  browser_pid="$!"
  sleep 2
  if ! kill -0 "$browser_pid" >/dev/null 2>&1 && ! pgrep -x chromium >/dev/null 2>&1; then
    warn_no_x11 "chromium exited immediately. See /tmp/openadsim-browser.log in the container."
  fi
fi

wait "$streamlit_pid"
