"""Read/sync a brand's supported comment inbox without proposing remote effects."""
from datetime import datetime,timezone
from pathlib import Path
import typer
import yaml
from socialctl.inbox import resolve_since,sync_brand_inbox
from socialctl.read_budget import ReadBudget
from socialctl.read_reports import emit_report,ReadReport,safe_error,read_exit_code
from socialctl.models import Platform


def register(app,default_root):
    from socialctl.engagement_cli import register as register_engagement
    register_engagement(app,default_root)
    from socialctl.brands import cargar_brand,BrandNoEncontrada,NombreDeMarcaInvalido,AccountsInvalido
    @app.command('inbox')
    def inbox(brand:str=typer.Option(...,'--brand'),since:str=typer.Option(...,'--since'),timezone_name:str|None=typer.Option(None,'--timezone'),json_output:bool=typer.Option(False,'--json'),timeout:float=typer.Option(300,'--timeout',help='Total read budget in seconds; use --only to give one platform the full budget.'),only:list[Platform]=typer.Option(None,'--only',help='Read only selected platforms; repeat to select more than one.'),root:Path=typer.Option(default_root,'--root')):
        try:
            selected=cargar_brand(root,brand)
            zone=timezone_name
            config=selected.raiz/'inbox.yml'
            if zone is None and config.exists():
                raw=yaml.safe_load(config.read_text())
                if not isinstance(raw,dict) or set(raw)!={'version','timezone'} or type(raw['version']) is not int or raw['version']!=1:raise ValueError('inbox.yml requires version 1 and timezone')
                zone=raw['timezone']
                if not isinstance(zone,str) or not zone:raise ValueError('inbox.yml timezone must be an IANA name')
            start=resolve_since(since,zone,datetime.now(timezone.utc))
            budget=ReadBudget(timeout)
        except (ValueError,OSError,yaml.YAMLError,BrandNoEncontrada,NombreDeMarcaInvalido,AccountsInvalido) as exc:
            if json_output:emit_report(ReadReport(status='error',errors=[{'code':'invalid_input','message':safe_error(str(exc))}]))
            else:typer.echo(safe_error(str(exc)),err=True)
            raise typer.Exit(2)
        try:
            report=sync_brand_inbox(selected,since=start,budget=budget,**({"only":only} if only else {}))
        except Exception as exc:
            from socialctl.agent_errors import error_document
            report=ReadReport(status='error',errors=[error_document(exc,read_only=True)])
            if json_output:raise typer.Exit(emit_report(report))
            typer.echo(report.errors[0]['message'],err=True)
            raise typer.Exit(1)
        if json_output:raise typer.Exit(emit_report(report))
        typer.echo(f'Inbox for {brand} since {report.data["resolved_since"]}: {len(report.data["items"])} comments; {report.status}')
        for coverage in report.coverage:typer.echo(f'{coverage["platform"]}: {coverage["state"]}')
        for error in report.errors:typer.echo(error['message'],err=True)
        for row in report.data['items']:typer.echo(f'{row["platform"]} {row["comment_id"]} — {row["text"]}')
        raise typer.Exit(read_exit_code(report.status))
