FROM docker:27-cli AS docker-cli
FROM python:3.12-slim
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /shared
COPY local_ci/requirements.txt /tmp/local-ci-requirements.txt
RUN pip install --no-cache-dir -r /tmp/local-ci-requirements.txt
COPY local_ci /shared/local_ci
COPY config/application /shared/config/application
COPY templates/application /shared/templates/application
COPY scripts/edge /shared/scripts/edge
COPY scripts/unpack-shared-tools.sh /shared/scripts/unpack-shared-tools.sh
COPY phpcs.sh /shared/phpcs.sh
ENV LOCAL_CI_STATE=/state LOCAL_CI_SHARED_ROOT=/shared LOCAL_CI_PROJECTS_ROOT=/projects
ENTRYPOINT ["python", "-m", "local_ci"]
CMD ["serve", "--host", "0.0.0.0"]
