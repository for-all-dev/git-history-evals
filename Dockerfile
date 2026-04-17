FROM coqorg/coq:8.14.1

USER root

# Install Python 3, pip, and git
RUN apt-get update \
 && apt-get install -y python3 python3-pip python3-venv git \
 && rm -rf /var/lib/apt/lists/*

# Use a venv to avoid externally-managed-environment errors on Debian 12+
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install experiment dependencies into the venv explicitly
RUN /opt/venv/bin/pip install --no-cache-dir \
    anthropic \
    python-dotenv \
    typer \
    pydantic

# Make coqc available on PATH without needing opam init
RUN ln -s "$(find /home/coq/.opam -name coqc -type f | head -1)" /usr/local/bin/coqc

WORKDIR /workspace

CMD ["python3", "/workspace/experiments/run_experiment.py"]