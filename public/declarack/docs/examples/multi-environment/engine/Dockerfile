FROM docker:29.5.2-dind
RUN apk upgrade --no-cache && apk add --no-cache openssh-server python3
COPY entrypoint.sh /usr/local/bin/lab-entrypoint
COPY sshd_config /etc/ssh/sshd_config
RUN chmod +x /usr/local/bin/lab-entrypoint
ENTRYPOINT ["lab-entrypoint"]
CMD ["dockerd", "--host=unix:///var/run/docker.sock", "--metrics-addr=0.0.0.0:9323"]
