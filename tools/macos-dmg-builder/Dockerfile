FROM debian:bookworm-slim AS libdmg-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake curl zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /src/libdmg \
    && curl --fail --location --retry 5 --retry-delay 2 \
       https://codeload.github.com/planetbeing/libdmg-hfsplus/tar.gz/refs/heads/master \
       | tar -xz --strip-components=1 -C /src/libdmg \
    && cmake -S /src/libdmg -B /src/libdmg/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /src/libdmg/build --parallel

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates genisoimage zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=libdmg-builder /src/libdmg/build/dmg/dmg /usr/local/bin/dmg
COPY build-dmg.sh /usr/local/bin/build-dmg
RUN chmod +x /usr/local/bin/build-dmg

ENTRYPOINT ["/usr/local/bin/build-dmg"]
