"""Agent-friendly inbox review and drafts, with one explicit batch approval."""
import json
from pathlib import Path
import typer
import yaml

from socialctl.agent_errors import error_document
from socialctl.brands import cargar_brand
from socialctl.engagement import review_inbox,prepare_replies
from socialctl.management.changes import ChangeError
from socialctl.management.community_batch_cli import preview
from socialctl.read_budget import ReadBudget


def register(app,default_root):
    @app.command('inbox-review')
    def review(brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),json_output:bool=typer.Option(False,'--json')):
        try:
            value=review_inbox(cargar_brand(root,brand))
        except Exception as exc:
            value={'version':1,'status':'error','error':error_document(exc,read_only=True)}
            typer.echo(json.dumps(value));raise typer.Exit(1)
        if json_output:typer.echo(json.dumps(value,ensure_ascii=False,indent=2))
        else:
            typer.echo('Saved inbox observations; live coverage and current visibility are unverified.')
            for row in value['items']:typer.echo(f"{row['platform']} {row['comment_id']} | {row['reply_state']} | {row['text']}")

    @app.command('inbox-draft')
    def draft(file:Path=typer.Option(...,'--file'),brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),dry_run:bool=typer.Option(False,'--dry-run'),json_output:bool=typer.Option(False,'--json'),timeout:float=typer.Option(120,'--timeout')):
        prepared=[]
        try:
            if not dry_run:raise ChangeError('drafting requires --dry-run and complete preview before separate approval')
            selected=cargar_brand(root,brand)
            with file.open('rb') as stream:
                raw=stream.read(262145)
            if len(raw)>262144:raise ChangeError('reply file exceeds 256 KiB')
            batch=prepare_replies(selected,yaml.safe_load(raw),budget=ReadBudget(timeout),prepared=prepared)
        except (Exception,KeyboardInterrupt) as exc:
            value={'version':1,'status':'partial' if prepared else 'error','brand':brand,
                'batch':None,'prepared':prepared,'error':error_document(exc,read_only=True)}
            typer.echo(json.dumps(value,ensure_ascii=False,indent=2));raise typer.Exit(1)
        typer.echo(preview(batch),err=json_output)
        if json_output:typer.echo(json.dumps({'version':1,'status':'prepared','brand':brand,
            'batch':batch.model_dump(mode='json'),'prepared':prepared},ensure_ascii=False,indent=2))
