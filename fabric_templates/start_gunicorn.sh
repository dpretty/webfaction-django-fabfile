#!/bin/bash

NAME="%(application_name)s"
# Assume repo name is the same as application name...
REPO_NAME=$NAME
VENV_DIR="%(virtualenv_dir)s"
WEBAPP_DIR="%(webapp_dir)s"
SOCKFILE=$WEBAPP_DIR/run/gunicorn.sock
NUM_WORKERS=3
DJANGO_WSGI_MODULE=config.wsgi
PORT=%(port)s

# Activate the virtual environment
cd $VENV_DIR
source bin/activate
source bin/postactivate
export PYTHONPATH=$VENV_DIR/$REPO_NAME/$NAME:$PYTHONPATH

# Create the run directory if it doesn't exist
RUNDIR=$(dirname $SOCKFILE)
test -d $RUNDIR || mkdir -p $RUNDIR

# Start your Django Unicorn
# Programs meant to be run under supervisor should not daemonize themselves (do not use --daemon)
exec bin/gunicorn ${DJANGO_WSGI_MODULE}:application \
  --name $NAME \
  --workers $NUM_WORKERS \
  --log-level=debug \
  --bind=0.0.0.0:$PORT
