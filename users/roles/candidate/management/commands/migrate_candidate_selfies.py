"""Migra selfies de candidato do caminho PÚBLICO legado pro storage privado.

Fundo: `_save_selfie` do candidato gravava em `media/candidate/<external_id>/selfie.<ext>` — um
caminho PÚBLICO (o prefixo `candidate` não está em MEDIA_PRIVATE_PREFIXES) e enumerável pelo
external_id. O fix aponta novos uploads pra `selfie/<token>.<ext>` (privado, gate de dono). Este
comando reescreve os registros ANTIGOS: copia o arquivo pro storage privado via `save_media`,
atualiza `Candidate.selfie_image` e apaga o arquivo público. Idempotente (só age em quem ainda
aponta pra `candidate/`), rodar 1× no deploy.

    python manage.py migrate_candidate_selfies          # aplica
    python manage.py migrate_candidate_selfies --dry-run # só relata
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.media import save_media
from users.roles.candidate.models import Candidate


class Command(BaseCommand):
    help = "Move selfies de candidato do caminho público legado (candidate/) pro privado (selfie/)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **o):
        dry = o["dry_run"]
        qs = Candidate.objects.filter(selfie_image__startswith="candidate/").exclude(
            selfie_image=""
        )
        moved = missing = 0
        for cand in qs.iterator():
            old_rel = cand.selfie_image
            old_fp = Path(settings.MEDIA_ROOT) / old_rel
            ext = old_rel.rsplit(".", 1)[-1] if "." in old_rel else "jpg"
            if not old_fp.exists():
                # registro aponta pra arquivo que não existe mais — só limpa o caminho público
                missing += 1
                self.stdout.write(f"  SEM ARQUIVO {old_rel} (candidate={cand.external_id})")
                if not dry:
                    cand.selfie_image = ""
                    cand.save(update_fields=["selfie_image"])
                continue
            if dry:
                self.stdout.write(f"  moveria {old_rel} → selfie/<token>.{ext}")
                moved += 1
                continue
            new_rel = save_media(prefix="selfie", data=old_fp.read_bytes(), ext=ext)
            cand.selfie_image = new_rel
            cand.save(update_fields=["selfie_image"])
            old_fp.unlink(missing_ok=True)
            moved += 1
            self.stdout.write(f"  {old_rel} → {new_rel}")
        verb = "moveria" if dry else "movidas"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: {moved} selfie(s); {missing} sem arquivo (caminho limpo)."
            )
        )
