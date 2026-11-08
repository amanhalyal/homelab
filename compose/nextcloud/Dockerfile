# The official Nextcloud image does not ship ffmpeg, and Nextcloud shells out to
# it to extract a frame for video previews. Without it the mobile app got a 404
# for every video preview request.
FROM nextcloud:33.0.6-apache
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
