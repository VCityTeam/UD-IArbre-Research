FROM anaconda/miniconda

ENV CONDA_PLUGINS_AUTO_ACCEPT_TOS=true

WORKDIR /app

RUN apt-get update && \
    apt-get install -y ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY environment.yml .

RUN conda env create -f environment.yml

RUN conda run -n shadow_modeling conda install -y ca-certificates certifi openssl

COPY . .

ENTRYPOINT ["conda", "run", "--no-capture-output", "-n", "shadow_modeling", "python"]

CMD ["Method2.py"]
