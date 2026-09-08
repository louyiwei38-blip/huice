module.exports = {
  apps: [
    {
      name: "range-mr",
      script: "./run_server.py",
      interpreter: "./.venv/bin/python",
      cwd: __dirname,
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_restarts: 20,
      min_uptime: "10s",
      restart_delay: 5000,
      kill_timeout: 5000,
      max_memory_restart: "300M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
      error_file: "./logs/pm2-error.log",
      out_file: "./logs/pm2-out.log",
      merge_logs: true,
      time: true,
    },
  ],
};
