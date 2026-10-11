"""One exact digest approves only the displayed immutable community batch."""
import json
from pathlib import Path
import typer
import yaml
from socialctl.management.changes import ChangeError
from socialctl.management.community_batches import prepare_community_batch,apply_community_batch,reconcile_community_batch,CommunityBatchStore


def preview(batch):
    return 'DRY-RUN — FULL COMMUNITY BATCH PREVIEW\n'+batch.model_dump_json(indent=2)+'\nEvery displayed child effect requires exact approval. No rollback is promised.\nExact batch fingerprint: '+batch.fingerprint


def register(changes_app,default_root):
    from socialctl.management.cli import _brand,_fail
    @changes_app.command('prepare-batch')
    def prepare(file:Path=typer.Option(...,'--file'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),dry_run:bool=typer.Option(False,'--dry-run')):
        try:
            selected=_brand(root,brand)
            raw=yaml.safe_load(file.read_text())
            if not isinstance(raw,dict) or set(raw)!={'version','children'} or raw['version']!=1: raise ChangeError('expected version 1 and explicit children')
            batch=prepare_community_batch(selected,raw['children'])
        except (ChangeError,OSError,ValueError) as exc:_fail(str(exc))
        typer.echo(preview(batch))

    @changes_app.command('apply-batch')
    def apply(batch_id:str,approval:str|None=typer.Option(None,'--approval'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root')):
        selected=_brand(root,brand)
        try:
            batch=CommunityBatchStore(selected.raiz).load(batch_id)
            typer.echo(preview(batch))
            if approval is None: approval=typer.prompt('Enter the exact batch fingerprint to approve')
            result=apply_community_batch(selected,batch_id,approval)
        except (ChangeError,OSError,ValueError) as exc:_fail(str(exc))
        typer.echo(result.model_dump_json(indent=2))
        if result.status!='verified':raise typer.Exit(1)

    @changes_app.command('reconcile-batch')
    def reconcile(batch_id:str,brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root')):
        try:result=reconcile_community_batch(_brand(root,brand),batch_id)
        except (ChangeError,OSError,ValueError) as exc:_fail(str(exc))
        typer.echo(result.model_dump_json(indent=2))
        if result.status!='verified':raise typer.Exit(1)
