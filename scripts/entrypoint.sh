#!/bin/bash

. /kb/deployment/user-env.sh

python ./scripts/prepare_deploy_cfg.py ./deploy.cfg ./work/config.properties

if [ -f ./work/token ] ; then
  export KB_AUTH_TOKEN=$(<./work/token)
fi

if [ $# -eq 0 ] ; then
  sh ./scripts/start_server.sh
elif [ "${1}" = "test" ] ; then
  echo "Run Tests"
  make test
elif [ "${1}" = "async" ] ; then
  # Modern KBase SDK: always use direct execution via bin script
  # Arguments are passed via remaining args (job_id, callback_url)
  echo "Async execution: calling bin script"
  shift  # Remove "async" from args
  exec ./bin/run_KBDatalakeDashboard_async_job.sh "$@"
elif [ "${1}" = "init" ] ; then
  echo "Initialize module"
  mkdir -p "/data"
  touch "/data/__READY__"
elif [ "${1}" = "bash" ] ; then
  bash
elif [ "${1}" = "report" ] ; then
  export KB_SDK_COMPILE_REPORT_FILE=./work/compile_report.json
  make compile
else
  # If we get here with arguments, assume it's direct async execution
  # KBase JobRunner passes: job_id, callback_url, job_params
  echo "Direct async execution mode - calling bin script with arguments"
  exec ./bin/run_KBDatalakeDashboard_async_job.sh "$@"
fi
