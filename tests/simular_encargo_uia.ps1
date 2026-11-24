# Ejecuta el controlador real con elementos en memoria, incluso en la ruta de envío.
param([string]$Puente)
$ErrorActionPreference = 'Stop'
$tokens = $null
$errores = $null
$programa = [System.Management.Automation.Language.Parser]::ParseFile($Puente,[ref]$tokens,[ref]$errores)
if ($errores) { throw ($errores.Message -join ', ') }
$funciones = $programa.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] },$false)
foreach ($f in $funciones | Where-Object { $_.Name -in 'Ejecutar','TextoNormal','MismoTexto','RevisarAdjuntos','GrupoDelCompositor' }) {
    . ([ScriptBlock]::Create($f.Extent.Text))
}
class ElementoPrueba {
    static [object]$FocusedElement
    static [object] FromHandle([IntPtr]$id) { return [pscustomobject]@{Hwnd=11} }
}
$ae = [ElementoPrueba]
$vp = 'valor'
$ip = 'invocar'
function Get-Process { param($Id) return @{ProcessName='Claude'} }
function Activa { return $caso -ne 'cambio de ventana' -or !$tocado }
function Campo { if ($caso -ne 'sin campo') { return $campoFalso } }
function Igual($a,$b) { return $null -ne $a -and $null -ne $b -and [object]::ReferenceEquals($a,$b) }
function Patron($el,$tipo) { return $el }
function BotonEnviar($campo) { if ($caso -ne 'sin boton') { return $botonFalso } }
function Start-Sleep { param($Milliseconds) }

Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$ctrl = [Windows.Automation.ControlType]
$scope = [Windows.Automation.TreeScope]::Descendants
$arbol = [pscustomobject]@{}
$arbol | Add-Member ScriptMethod GetParent { param($el) return $el.Padre }
function Nodo($etiqueta, $tipo, $id='') {
    return [pscustomobject]@{Current=@{Name=$etiqueta; ControlType=$tipo; AutomationId=$id}}
}
foreach ($caso in 'normal','pegar','sin campo','ocupado','adjunto pendiente','chip','nombre de archivo','adjunto tardio','grupo desconocido','cambio de ventana','texto distinto','sin boton','proceso distinto') {
    $q = @{texto="Hola`nMundo"; proceso='claude'; hwnd=11; pid=12; operacion='enviar'}
    if ($caso -eq 'pegar') { $q.operacion='pegar' }
    if ($caso -eq 'proceso distinto') { $q.proceso='chatgpt' }
    $tocado=$false
    $enviando=$false
    $invocaciones=0
    $conversacion=Nodo 'Historial' $ctrl::Document
    $conversacion | Add-Member ScriptMethod FindAll { throw 'Se inspecciono el historial por error' }
    $grupoFalso=Nodo 'Redactor' $ctrl::Group $(if ($caso -ne 'grupo desconocido') { 'composer-container' } else { '' })
    $grupoFalso | Add-Member NoteProperty Padre $conversacion
    $grupoFalso | Add-Member ScriptMethod FindAll {
        param($scope,$condicion)
        if ($caso -eq 'adjunto pendiente') { return (Nodo 'Quitar' $ctrl::Button) }
        if ($caso -eq 'chip') { return (Nodo '' $ctrl::Group 'attachment-pill') }
        if ($caso -eq 'nombre de archivo') { return (Nodo 'Material.docx' $ctrl::ListItem) }
        if ($caso -eq 'adjunto tardio' -and $tocado) { return (Nodo 'Remove' $ctrl::Button) }
        return (Nodo 'Agregar archivos' $ctrl::Button)
    }
    $campoFalso=[pscustomobject]@{Padre=$grupoFalso; Current=@{Value=$(if($caso -eq 'ocupado') { 'mi borrador' } else { '' })}}
    $campoFalso | Add-Member ScriptMethod SetFocus { [ElementoPrueba]::FocusedElement=$this }
    $campoFalso | Add-Member ScriptMethod SetValue {
        param($nuevo)
        $this.Current.Value=if ($caso -eq 'texto distinto') { 'otro' } else { $nuevo }
    }
    $botonFalso=[pscustomobject]@{}
    $botonFalso | Add-Member ScriptMethod Invoke { $script:invocaciones++ }
    $resultado=Ejecutar
    if ($caso -eq 'normal') {
        if ($resultado.estado -ne 'enviado' -or $invocaciones -ne 1) { throw 'No emitio un solo envio autorizado' }
    } elseif ($caso -eq 'pegar') {
        if ($resultado.estado -ne 'pegado' -or $invocaciones -ne 0) { throw 'Pegar produjo envio' }
    } elseif (!$resultado.error -or $invocaciones) { throw "Se ejecuto un envio para $caso" }
    if ($caso -in 'adjunto pendiente','adjunto tardio','chip','nombre de archivo' -and $resultado.error -ne 'adjunto') { throw "No se detecto el adjunto para $caso" }
    if ($caso -eq 'grupo desconocido' -and $resultado.error -ne 'compositor') { throw 'No se detecto compositor desconocido' }
    if ($caso -in 'sin campo','ocupado','adjunto pendiente','chip','nombre de archivo','grupo desconocido','proceso distinto' -and $tocado) { throw "Se escribio para $caso" }
}
