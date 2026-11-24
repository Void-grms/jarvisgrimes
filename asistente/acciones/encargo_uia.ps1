# Operaciones de accesibilidad sobre un compositor de ChatGPT o Claude de escritorio.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();' -Name Ventana -Namespace Encargo
$q = [Console]::In.ReadToEnd() | ConvertFrom-Json
$ae = [Windows.Automation.AutomationElement]
$scope = [Windows.Automation.TreeScope]::Descendants
$arbol = [Windows.Automation.TreeWalker]::RawViewWalker
$ctrl = [Windows.Automation.ControlType]
$vp = [Windows.Automation.ValuePattern]::Pattern
$tp = [Windows.Automation.TextPattern]::Pattern
$ip = [Windows.Automation.InvokePattern]::Pattern

function Activa {
    return [Encargo.Ventana]::GetForegroundWindow().ToInt64() -eq $q.hwnd -and
           $raiz.Current.ProcessId -eq $q.pid
}
function Igual($a, $b) { return $a -and $b -and [Windows.Automation.Automation]::Compare($a,$b) }
function Patron($el, $tipo) {
    $valor = $null
    if ($el.TryGetCurrentPattern($tipo,[ref]$valor)) { return $valor }
}
function EsCampo($el) {
    $v = $el.Current
    if ($v.IsOffscreen -or !$v.IsEnabled -or $v.IsPassword -or !$v.IsKeyboardFocusable) { return $false }
    if ($v.ControlType -notin $ctrl::Edit,$ctrl::Document,$ctrl::Custom) { return $false }
    $idConocido = $v.AutomationId -match '^(prompt-textarea|chat-input|chat-prompt|message-input|composer-input)$'
    $etiqueta = $v.Name.Trim().TrimEnd('.','…','?').TrimStart('¿').Trim()
    $nombreConocido = $etiqueta -match '^(message( (ChatGPT|Claude))?|reply to Claude|ask anything|type (a |your )?message|send a message|mensaje( (para|a) (ChatGPT|Claude))?|escribe (tu |un )?mensaje|envía un mensaje( a (ChatGPT|Claude))?|responde a Claude|pregunta lo que quieras|en qué puedo ayudarte)$'
    if (!$idConocido -and !$nombreConocido) { return $false }
    $valor = Patron $el $vp
    return $null -ne $valor -and !$valor.Current.IsReadOnly
}
function CondicionTipo($tipo) {
    return [Windows.Automation.PropertyCondition]::new($ae::ControlTypeProperty,$tipo)
}
function Campo {
    $tipos = [Windows.Automation.OrCondition]::new(
        [Windows.Automation.Condition[]]@((CondicionTipo $ctrl::Edit),(CondicionTipo $ctrl::Custom),(CondicionTipo $ctrl::Document)))
    $campos = @($raiz.FindAll($scope,$tipos) | Where-Object { EsCampo $_ })
    if ($campos.Count -eq 1) { return $campos[0] }
}
function BotonEnviar($campo) {
    $padre = $campo
    for ($i=0; $i -lt 8; $i++) {
        $padre = $arbol.GetParent($padre)
        if (!$padre -or (Igual $raiz $padre) -or $padre.Current.ControlType -eq $ctrl::Document) { return }
        $botones = @($padre.FindAll($scope,(CondicionTipo $ctrl::Button)) | Where-Object {
            $v = $_.Current
            $v.IsEnabled -and !$v.IsOffscreen -and
                ($v.Name.Trim() -match '^(enviar( (el )?mensaje)?|send( (message|prompt))?)$' -or
                (!$v.Name.Trim() -and $v.AutomationId -match '^(send-button|send-message-button)$')) -and
                $null -ne (Patron $_ $ip)
        })
        if ($botones.Count -eq 1) { return $botones[0] }
        if ($botones.Count -gt 1) { return }
    }
}
# Busca un contenedor del compositor. La conversación completa puede mostrar
# archivos de mensajes anteriores, que no pertenecen al borrador actual.
function GrupoDelCompositor($campo) {
    $grupo = $campo
    for ($i=0; $i -lt 8; $i++) {
        $grupo = $arbol.GetParent($grupo)
        if (!$grupo -or (Igual $raiz $grupo) -or $grupo.Current.ControlType -eq $ctrl::Document) { return }
        $v = $grupo.Current
        if ($v.AutomationId -match '^(?:chat[-_])?(?:composer|message[-_]form)(?:[-_](?:form|container|root|parent))?$' -or
            $v.ClassName -match '(?:^|\s)(?:chat[-_])?composer[-_](?:form|container|root|parent)(?:\s|$)' -or
            $v.Name -match '^(message composer|chat composer|redactor de mensajes|compositor de mensajes)$' -or
            $v.LocalizedControlType -match '^(form|formulario)$') {
            return $grupo
        }
    }
}
function RevisarAdjuntos($campo) {
    $grupo = GrupoDelCompositor $campo
    if (!$grupo) { return 'compositor' }
    foreach ($el in $grupo.FindAll($scope,[Windows.Automation.Condition]::TrueCondition)) {
        $v = $el.Current
        $id = [string]$v.AutomationId
        if ($id -match '(attachment|file|upload).*(remove|delete|chip|pill|preview|thumbnail)|(remove|delete|uploaded).*(attachment|file|upload)' -or
            $v.ControlType -eq $ctrl::ProgressBar) { return 'adjunto' }
        $etiqueta = ([string]$v.Name).Trim()
        if ($v.ControlType -in $ctrl::Image,$ctrl::ListItem,$ctrl::DataItem,$ctrl::Group,$ctrl::Text -and
            $etiqueta -match '^(?:(?:attachment|archivo adjunto|imagen adjunta)\s*[:·]|[^\r\n]{1,240}\.(?:pdf|docx?|xlsx?|pptx?|txt|md|csv|png|jpe?g|webp|gif|zip)$)') {
            return 'adjunto'
        }
        if ($v.ControlType -eq $ctrl::Button -and
            $etiqueta -match '^(?:remove|delete|quitar|eliminar|borrar)(?:$|\s+.*(?:attachment|file|image|adjunto|archivo|imagen|\.\w{1,8}$))') {
            return 'adjunto'
        }
    }
}
function TextoNormal($texto) {
    # Los proveedores pueden representar un salto de línea como LF o CRLF.
    return ([string]$texto).Replace("`r`n","`n").Replace("`r","`n")
}
function MismoTexto($a,$b) {
    return [string]::Equals((TextoNormal $a),(TextoNormal $b),[StringComparison]::Ordinal)
}
function Seleccion {
    $docs = $raiz.FindAll($scope,(CondicionTipo $ctrl::Document))
    $textos = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $foco = $ae::FocusedElement
    if ($foco -and ($foco.Current.IsPassword -or $foco.Current.ControlType -eq $ctrl::Edit)) {
        return @{error='sin_seleccion'}
    }
    foreach ($doc in $docs) {
        if ($doc.Current.IsOffscreen -or $doc.Current.IsPassword -or (EsCampo $doc)) { continue }
        $pat = Patron $doc $tp
        if (!$pat) { continue }
        $rangos = @($pat.GetSelection())
        if ($rangos.Count -ne 1) { continue }
        $sel = $rangos[0].GetText(-1)
        if (![string]::IsNullOrWhiteSpace($sel)) { $null = $textos.Add($sel) }
    }
    if ($textos.Count -eq 1 -and (Activa)) { return @{texto=@($textos)[0]} }
    return @{error='sin_seleccion'}
}
function Ejecutar {
    if ($q.proceso -notin 'claude','chatgpt') { return @{error='ventana'} }
    $script:raiz = $ae::FromHandle([IntPtr][long]$q.hwnd)
    if (!(Activa) -or (Get-Process -Id $q.pid).ProcessName -ine $q.proceso) { return @{error='ventana'} }
    if ($q.operacion -eq 'seleccion') { return Seleccion }
    if ($q.operacion -notin 'enviar','pegar' -or [string]::IsNullOrWhiteSpace($q.texto)) { return @{error='campo'} }
    $campo = Campo
    if (!$campo -or !(Activa)) { return @{error='campo'} }
    $valor = Patron $campo $vp
    if ($valor.Current.Value -ne '') { return @{error='ocupado'} }
    $problema = RevisarAdjuntos $campo
    if ($problema) { return @{error=$problema} }
    $campo.SetFocus()
    if (!(Activa) -or !(Igual $campo $ae::FocusedElement) -or ($valor.Current.Value -ne '')) { return @{error='campo'} }
    $script:tocado = $true
    $valor.SetValue($q.texto)
    if (!(MismoTexto $valor.Current.Value $q.texto)) { return @{error='borrador'} }
    if ($q.operacion -eq 'pegar') { return @{estado='pegado'} }
    # El compositor puede habilitar el botón al procesar el cambio de texto.
    for ($i=0; $i -lt 6; $i++) {
        if (!(Activa) -or !(Igual $campo $ae::FocusedElement)) { return @{error='borrador'} }
        $boton = BotonEnviar $campo
        if ($boton) { break }
        Start-Sleep -Milliseconds 120
    }
    if (!$boton -or !(Igual $campo (Campo)) -or !(Igual $boton (BotonEnviar $campo)) -or
        !(Activa) -or !(Igual $campo $ae::FocusedElement) -or
        !(MismoTexto $valor.Current.Value $q.texto)) { return @{error='borrador'} }
    $problema = RevisarAdjuntos $campo
    if ($problema) { return @{error=$problema} }
    if (!(Activa) -or !(Igual $campo $ae::FocusedElement) -or
        !(MismoTexto $valor.Current.Value $q.texto)) { return @{error='borrador'} }
    $script:enviando = $true
    (Patron $boton $ip).Invoke()
    return @{estado='enviado'}
}
$tocado = $false
$enviando = $false
try { $respuesta = Ejecutar } catch {
    $respuesta = @{error=$(if ($enviando) { 'incierto' } elseif ($tocado) { 'borrador' } else { 'campo' })}
}
[Console]::WriteLine(($respuesta | ConvertTo-Json -Compress))
