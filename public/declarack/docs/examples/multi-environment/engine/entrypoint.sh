#!/bin/sh
set -eu
if [ "${LAB_SSH:-0}" = 1 ]; then
  mkdir -p /root/.ssh /run/sshd /var/lib/lab-ssh
  chmod 700 /root/.ssh
  cp /run/lab/authorized_keys /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
  if [ ! -s /var/lib/lab-ssh/ssh_host_ed25519_key ]; then
    ssh-keygen -q -t ed25519 -N '' -f /var/lib/lab-ssh/ssh_host_ed25519_key
  fi
  /usr/sbin/sshd -f /etc/ssh/sshd_config
fi
# Passing an explicit dockerd command suppresses the base image's default TCP API.
exec /usr/local/bin/dockerd-entrypoint.sh "$@"
