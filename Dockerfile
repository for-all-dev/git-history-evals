FROM coqorg/coq:8.14.1

USER root

# Install Python 3 (venv) and git — pip comes bundled inside the venv
RUN apt-get update \
 && apt-get install -y python3 python3-venv git \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install experiment dependencies into the venv explicitly
RUN /opt/venv/bin/pip install --no-cache-dir \
    anthropic \
    python-dotenv \
    typer \
    pydantic

# Make Coq tools available on PATH without needing opam init
RUN COQBIN=$(dirname "$(find /home/coq/.opam -name coqc -type f | head -1)") && \
    ln -s "$COQBIN/coqc"         /usr/local/bin/coqc         && \
    ln -s "$COQBIN/coq_makefile" /usr/local/bin/coq_makefile && \
    ln -s "$COQBIN/coqdep"       /usr/local/bin/coqdep       && \
    ln -s "$COQBIN/coqtop"       /usr/local/bin/coqtop

WORKDIR /workspace

CMD ["python3", "/workspace/experiments/run_experiment.py"]