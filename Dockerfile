FROM python:3.10-slim-bullseye
LABEL us.kbase.python="3.10"
LABEL maintainer="chenry@anl.gov"

ENV TZ=Etc/UTC
ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PROGRESS_BAR=off

# -----------------------------------------
# Install system dependencies
# -----------------------------------------
RUN apt-get update
RUN apt-get install -y build-essential \
                       git \
                       openjdk-11-jre \
                       unzip \
                       htop \
                       wget \
                       curl \
                       gcc

# Copy in the SDK
COPY --from=kbase/kb-sdk:1.2.1 /src /sdk
RUN sed -i 's|/src|/sdk|g' /sdk/bin/*
ENV PATH=/sdk/bin:$PATH

ADD requirements_kbase.txt /tmp/requirements_kbase.txt
RUN /usr/local/bin/pip install -r /tmp/requirements_kbase.txt
ADD biokbase /opt/conda/lib/python3.11/site-packages
ADD biokbase/user-env.sh /kb/deployment/user-env.sh

RUN mkdir -p /deps
RUN pip install --upgrade pip
# -----------------------------------------
# Install KBUtilLib for shared utilities
# This provides common KBase functionality:
# - KBWSUtils: Workspace operations
# - KBGenomeUtils: Genome parsing and analysis
# - KBModelUtils: Metabolic model utilities
# - KBCallbackUtils: Callback server handling
# - SharedEnvUtils: Configuration and token management
# -----------------------------------------

# -----------------------------------------
# Copy module files
# -----------------------------------------

ADD requirements.txt /tmp/requirements.txt

COPY ./ /kb/module
RUN mkdir -p /kb/module/work
RUN chmod -R a+rw /kb/module
RUN /usr/local/bin/pip install -r /tmp/requirements.txt

# @chenry
WORKDIR /deps
RUN git clone https://github.com/cshenry/ModelSEEDpy.git && pip install --use-deprecated=legacy-resolver -e ModelSEEDpy
RUN echo '0' >/dev/null && cd /deps && \
    git clone https://github.com/cshenry/cobrakbase.git && \
    cd cobrakbase && git checkout 68444e46fe3b68482da80798642461af2605e349
RUN echo '4' >/dev/null && cd /deps && \
    git clone https://github.com/cshenry/KBUtilLib.git && \
    cd KBUtilLib && pip install -e .

WORKDIR /kb/module

# Compile the module
RUN make all

ENTRYPOINT [ "./scripts/entrypoint.sh" ]

CMD [ ]
