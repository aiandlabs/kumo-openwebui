# Open Terminal plus Japanese fonts.
# Open WebUI 0.13 previews .docx files in the chat by converting them with the
# terminal's LibreOffice. Without a Japanese font, Japanese text renders as boxes.
FROM ghcr.io/open-webui/open-terminal@sha256:02b8701e47a0d5aaf5c8b5ff2b8588975eec9bc452df2fa32141c61c8910ea24
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-ipaexfont-gothic fonts-ipaexfont-mincho fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f
USER user
