"""Local diagnostics across ordinary publication routes, preserving partial results."""
from pathlib import Path

from socialctl.adapters import ADAPTADORES
from socialctl.media import leer_media,MediaNoEncontrada,MediaInvalida
from socialctl.media_requirements import SOURCES,technical_issues
from socialctl.models import MediaAsset,MediaKind,Platform
from socialctl.postfile import _ruta,_cargar_datos,_construir_platform_post,_validar_nombre_de_media,PostInvalido
from socialctl.publication_policy import validate_derivation
from socialctl.read_reports import safe_error


def issue(code,message,*,field='media',action='supply_compatible_media'):
    return {'code':code,'field':field,'action':action,'retryable':False,'message':safe_error(message)}


def check_post(brand,slug,only=()):
    path=_ruta(brand,slug)
    raw=_cargar_datos(path)
    blocks=raw.get('platforms')
    if not isinstance(blocks,dict) or not blocks:raise PostInvalido('post requires platform mappings')
    try:available={Platform(p) for p in blocks}
    except ValueError:raise PostInvalido('unknown platform in post') from None
    selected=set(only) if only else available
    if not selected<=available:raise PostInvalido('selected platform absent from post')
    cache={};platforms={}
    for platform in sorted(selected,key=lambda p:p.value):
        diagnostics=[];assets=[]
        def read(file,*,ruta_relativa):
            key=str(file.resolve())
            if key not in cache:
                try:
                    asset=leer_media(file,ruta_relativa=ruta_relativa)
                    cache[key]=(asset,None)
                except (MediaNoEncontrada,FileNotFoundError):
                    cache[key]=(None,issue('media_missing','Supplied file is missing.'))
                except MediaInvalida as exc:
                    action='install_probe' if exc.code=='probe_unavailable' else 'inspect_file' if exc.code=='probe_timeout' else 'supply_compatible_media'
                    cache[key]=(None,issue(exc.code,'Local media inspection did not produce usable metadata.',action=action))
                except (OSError,ValueError,TypeError):
                    cache[key]=(None,issue('media_unreadable','Supplied file could not be inspected.'))
            asset,error=cache[key]
            record={'file':safe_error(ruta_relativa),'status':'blocked' if error else 'inspected'}
            if error:
                diagnostics.append({**error,'file':safe_error(ruta_relativa)})
                record['issue']=error
                asset=MediaAsset(path=file,kind=MediaKind.VIDEO,ruta_relativa=ruta_relativa)
            else:
                record['metadata']={'kind':asset.kind.value,'width':asset.width,'height':asset.height,
                    'duration_s':asset.duration_s,'size_bytes':asset.size_bytes,'technical':asset.probe}
            assets.append(record)
            return asset
        block=blocks[platform.value]
        prepared={}
        # Inspect each path independently, even when other post fields are invalid.
        if isinstance(block,dict) and isinstance(block.get('media',[]),list):
            safe_names=[]
            for index,name in enumerate(block.get('media',[])):
                try:
                    file=_validar_nombre_de_media(brand.raiz/'media',name)
                    relative=file.relative_to(brand.raiz/'media').as_posix()
                except (ValueError,TypeError,OSError):
                    error=issue('media_path_invalid','Supply a relative file path inside the brand media directory.')
                    label=safe_error(name) if isinstance(name,str) else f'entry {index+1}'
                    assets.append({'file':label,'status':'blocked','issue':error})
                    diagnostics.append({**error,'file':label})
                    continue
                prepared[str(file)]=read(file,ruta_relativa=relative)
                safe_names.append(name)
            block={**block,'media':safe_names}
        try:
            post=_construir_platform_post(platform,block,brand.raiz/'media',path,
                media_reader=lambda file,**kwargs:prepared[str(file)])
            errors=ADAPTADORES[platform]().validate(post,brand)+validate_derivation(post,require_origin=True)
            detailed=[i for asset in post.media for i in technical_issues(platform,asset)]
            detailed_messages={i['message'] for i in detailed}
            diagnostics.extend({**i,'field':'media'} for i in detailed)
            for error in errors:
                if error.motivo in detailed_messages:continue
                action=('configure_account' if error.campo in {'cuenta','auditoria','credenciales'}
                        else 'supply_compatible_media' if error.campo in {'media','duracion','formato'} else 'review_post')
                diagnostics.append(issue('route_validation',error.motivo,field=error.campo,action=action))
        except Exception:
            diagnostics.append(issue('post_invalid','Review the selected platform block and contained media paths.',field='post',action='review_post'))
        platforms[platform.value]={'status':'blocked' if diagnostics else 'passed','assets':assets,'issues':diagnostics,
            'requirements_source':SOURCES.get(platform.value,'SocialCli configured route policy'),
            'coverage':{'provider_acceptance_verified':False,'all_file_bytes_decoded':False,
                'unchecked':['current account eligibility and grants','remote URL availability',
                    'full decoder integrity','provider processing and publication outcome'],
                'local_policy':'Configured size/duration/route limits also apply; they are not a complete provider specification.'}}
    passed=sum(row['status']=='passed' for row in platforms.values())
    return {'version':1,'brand':brand.nombre,'slug':slug,'status':'passed' if passed==len(platforms) else 'partial' if passed else 'blocked',
        'scope':'ordinary post.yml routes; native manifests, Stories and groups have separate validators',
        'server_media_storage':False,'provider_eligibility_verified':False,'files_modified':False,'platforms':platforms}


def register(media_app,default_root):
    import json
    import typer
    from socialctl.brands import cargar_brand
    from socialctl.agent_errors import error_document
    @media_app.command('check')
    def check(slug:str,brand:str=typer.Option(...,'--brand'),root:Path=typer.Option(default_root,'--root'),only:list[Platform]=typer.Option(None,'--only'),json_output:bool=typer.Option(False,'--json')):
        try:value=check_post(cargar_brand(root,brand),slug,only)
        except (Exception,KeyboardInterrupt) as exc:
            value={'version':1,'status':'error','error':error_document(exc,read_only=True)}
        if json_output:typer.echo(json.dumps(value,ensure_ascii=False,indent=2))
        elif 'platforms' in value:
            typer.echo('Local post preflight; no upload or approval. Provider acceptance remains unverified.')
            for platform,row in value['platforms'].items():
                typer.echo(f"{platform}: {row['status']}")
                for problem in row['issues']:typer.echo(f"  {problem['field']}: {problem['message']}")
        else:typer.echo(value['error']['message'])
        if value['status']!='passed':raise typer.Exit(1)
