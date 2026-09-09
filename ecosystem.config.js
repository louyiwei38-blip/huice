module.exports = {
  apps: [
    {
      name: 'rsibb',
      cwd: __dirname,
      script: '.venv/bin/python',
      args: '-u live.py --loop',
      watch: false,
      restart_delay: 5000,
      max_restarts: 20,
      max_memory_restart: '800M',
      error_file: 'logs/rsibb-error.log',
      out_file: 'logs/rsibb-out.log',
      merge_logs: true,
      time: true,
      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
  ],
};
