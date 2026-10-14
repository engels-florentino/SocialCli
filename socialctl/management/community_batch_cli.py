"""One exact digest approves only the displayed immutable community batch."""
from pathlib import Path
import typer
import yaml
from socialctl.management.changes import ChangeError
from socialctl.operation_output import OperationOutput
from socialctl.management.community_batches import (prepare_community_batch,apply_community_batch,
    reconcile_community_batch,CommunityBatchStore,validate_batch)


def preview(batch):
    return 'DRY-RUN — FULL COMMUNITY BATCH PREVIEW\n'+batch.model_dump_json(indent=2)+'\nEvery displayed child effect requires exact approval. No rollback is promised.\nExact batch fingerprint: '+batch.fingerprint


def register(changes_app,default_root):
    @changes_app.command('prepare-batch')
    def prepare(file:Path=typer.Option(...,'--file'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),dry_run:bool=typer.Option(False,'--dry-run'),json_output:bool=typer.Option(False,'--json')):
        with OperationOutput(json_output) as output:
            selected=output.brand(root,brand)
            raw=yaml.safe_load(file.read_text())
            if not isinstance(raw,dict) or set(raw)!={'version','children'} or type(raw['version']) is not int or raw['version']!=1:
                raise ChangeError('expected version 1 and explicit children')
            batch=prepare_community_batch(selected,raw['children'])
            typer.echo(preview(batch),err=json_output)
            if json_output:output.emit(batch)

    @changes_app.command('apply-batch')
    def apply(batch_id:str,approval:str|None=typer.Option(None,'--approval'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),json_output:bool=typer.Option(False,'--json')):
        with OperationOutput(json_output) as output:
            selected=output.brand(root,brand)
            batch=CommunityBatchStore(selected.raiz).load(batch_id)
            validate_batch(selected,batch)
            approved=output.approve(batch,preview(batch),approval)
            output.begin_write()
            result=apply_community_batch(selected,batch_id,approved)
            output.emit(result)
            if result.status!='verified':raise typer.Exit(1)

    @changes_app.command('reconcile-batch')
    def reconcile(batch_id:str,brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),json_output:bool=typer.Option(False,'--json')):
        with OperationOutput(json_output,read_only=True) as output:
            selected=output.brand(root,brand)
            output.change_id=CommunityBatchStore(selected.raiz).load(batch_id).id
            result=reconcile_community_batch(selected,batch_id)
            output.emit(result)
            if result.status!='verified':raise typer.Exit(1)

    @changes_app.command('status-batch')
    def status(batch_id:str,brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),json_output:bool=typer.Option(False,'--json')):
        with OperationOutput(json_output,read_only=True) as output:
            selected=output.brand(root,brand)
            result=CommunityBatchStore(selected.raiz).load(batch_id)
            validate_batch(selected,result)
            output.emit(result)
