"""Áudio gravado no navegador -> formato aceito pelo WhatsApp (OGG com codec Opus, mono).

O Chrome grava em WebM/Opus e o Safari em MP4/AAC; o WhatsApp só aceita OGG/Opus para mensagem de voz. A conversão usa o
ffmpeg (do pacote imageio-ffmpeg, ou o ffmpeg do sistema se existir)."""
import shutil
import subprocess


class ErroAudio(Exception):
    pass


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


def para_ogg_opus(conteudo, mime=""):
    """Devolve bytes OGG/Opus. Quem já vem em OGG/Opus passa direto."""
    m = (mime or "").lower()
    if m.startswith("audio/ogg") and "opus" in m:
        return conteudo
    exe = _ffmpeg()
    if not exe:
        raise ErroAudio("Não encontrei o ffmpeg no servidor para converter o áudio.")
    try:
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", "48000",
                            "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", "pipe:1"], input=conteudo, capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise ErroAudio("Falha ao converter o áudio.") from e
    if r.returncode != 0 or not r.stdout:
        raise ErroAudio("Não consegui converter o áudio gravado.")
    return r.stdout
