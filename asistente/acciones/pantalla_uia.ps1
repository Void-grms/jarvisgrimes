$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
Add-Type -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow();' -Name Ventana -Namespace Jarvis

$q = [Console]::In.ReadToEnd() | ConvertFrom-Json
$ae = [Windows.Automation.AutomationElement]
$arbol = [Windows.Automation.TreeWalker]::RawViewWalker

function Activa { [Jarvis.Ventana]::GetForegroundWindow().ToInt64() -eq $q.hwnd }
function Id($el) { $el.GetRuntimeId() -join '.' }
function Tipo($el) { $el.Current.ControlType.ProgrammaticName -replace '^ControlType\.', '' }
function EnVentana($el) {
    while ($null -ne $el) {
        if ([Windows.Automation.Automation]::Compare($raiz, $el)) { return $true }
        $el = $arbol.GetParent($el)
    }
    return $false
}
function Patron($el, $patron) {
    $valor = $null
    if ($el.TryGetCurrentPattern($patron, [ref]$valor)) { return $valor }
}
function Detalle($el) {
    $v = $el.Current
    if ($v.IsPassword -or $v.IsOffscreen -or !$v.IsEnabled) { return }
    $tipoControl = Tipo $el
    $nombre = [string]$v.Name
    if ($nombre.Length -gt 160) { $nombre = $nombre.Substring(0,160) }
    $click = $null
    foreach ($c in 'Invoke','SelectionItem','Toggle','ExpandCollapse') {
        $p = $el.GetCurrentPropertyValue($ae::("Is${c}PatternAvailableProperty"))
        if ($p -eq $true) { $click = $c; break }
    }
    $edita = $false
    if ($tipoControl -in 'Edit','Document','ComboBox','Custom') {
        $vp = Patron $el ([Windows.Automation.ValuePattern]::Pattern)
        $edita = $null -ne $vp -and !$vp.Current.IsReadOnly
    }
    if (!$click -and $edita -and $v.IsKeyboardFocusable) { $click = 'Focus' }
    $padre = $arbol.GetParent($el)
    $claseWord = $false
    if ($v.HasKeyboardFocus -and !$edita -and $tipoControl -in 'Document','Custom','Pane','Edit') {
        $p = $el
        while ($null -ne $p -and ![Windows.Automation.Automation]::Compare($raiz,$p)) {
            if ($p.Current.ClassName -eq '_WwG') { $claseWord = $true; break }
            $p = $arbol.GetParent($p)
        }
    }
    return @{id=(Id $el); tipo=$tipoControl; nombre=$nombre; click=$click; edita=$edita;
             word=$claseWord; padre=$(if ($padre) { Id $padre }); foco=$v.HasKeyboardFocus}
}
function Activar($el,$modo) {
    switch ($modo) {
        'Focus' { $el.SetFocus() }
        'Invoke' { (Patron $el ([Windows.Automation.InvokePattern]::Pattern)).Invoke() }
        'SelectionItem' { (Patron $el ([Windows.Automation.SelectionItemPattern]::Pattern)).Select() }
        'Toggle' { (Patron $el ([Windows.Automation.TogglePattern]::Pattern)).Toggle() }
        'ExpandCollapse' {
            $pat = Patron $el ([Windows.Automation.ExpandCollapsePattern]::Pattern)
            if ($pat.Current.ExpandCollapseState -eq 'Expanded') { $pat.Collapse() } else { $pat.Expand() }
        }
    }
}
function Ejecutar {
    if (!(Activa)) { return @{error='ventana'} }
    $script:raiz = $ae::FromHandle([IntPtr][long]$q.hwnd)
    if ($raiz.Current.ProcessId -ne $q.pid) { return @{error='ventana'} }
    # Un cambio de título entre la selección y la ejecución puede indicar otra pestaña o documento.
    $titulo = $raiz.Current.Name
    if ($q.operacion -ne 'leer' -and $q.titulo -cne $titulo) { return @{error='ventana'} }
    $foco = $ae::FocusedElement
    if ($null -ne $foco -and !(EnVentana $foco)) { $foco = $null }
    $idFoco = if ($foco) { Id $foco } else { '' }
    $cond = [Windows.Automation.AndCondition]::new(
        [Windows.Automation.PropertyCondition]::new($ae::IsControlElementProperty,$true),
        [Windows.Automation.PropertyCondition]::new($ae::IsOffscreenProperty,$false),
        [Windows.Automation.PropertyCondition]::new($ae::IsEnabledProperty,$true))
    $elementos = @()
    if (!$q.soloFoco -and !($q.operacion -ne 'leer' -and $q.foco)) {
        $elementos = $raiz.FindAll([Windows.Automation.TreeScope]::Descendants, $cond)
    }
    if ($q.operacion -eq 'leer') {
        $controles = [Collections.Generic.List[object]]::new()
        # En árboles grandes, deja que el proveedor encuentre todas las etiquetas exactas.
        if ($elementos.Count -gt 360 -and $q.etiqueta) {
            $porNombre = [Windows.Automation.PropertyCondition]::new(
                $ae::NameProperty,$q.etiqueta,[Windows.Automation.PropertyConditionFlags]::IgnoreCase)
            $cond = [Windows.Automation.AndCondition]::new($cond,$porNombre)
            $elementos = $raiz.FindAll([Windows.Automation.TreeScope]::Descendants,$cond)
        }
        $limite = [Math]::Min($elementos.Count, 360)
        for ($i=0; $i -lt $limite; $i++) {
            try {
                $s = Detalle $elementos[$i]
                if ($null -ne $s) { $controles.Add($s) }
            } catch [Windows.Automation.ElementNotAvailableException] { continue }
        }
        $fichaFoco = if ($foco) { Detalle $foco }
        if (!(Activa)) { return @{error='ventana'} }
        return @{titulo=$titulo; controles=$controles; foco=$fichaFoco; incompleto=$elementos.Count -gt $limite}
    }

    if ($q.foco -and $q.control.id -cne $idFoco) { return @{error='foco'} }
    $objetivo = $null
    $boton = $null
    if ($q.foco) { $objetivo = $foco }
    if (!$objetivo -or $q.boton) {
        foreach ($el in $elementos) {
            $idActual = Id $el
            if ($idActual -ceq $q.control.id) { $objetivo = $el }
            if ($q.boton -and $idActual -ceq $q.boton.id) { $boton = $el }
            if ($objetivo -and (!$q.boton -or $boton)) { break }
        }
    }
    if (!$objetivo) { return @{error='control'} }
    $ficha = Detalle $objetivo
    if (!$ficha -or $ficha.nombre -cne $q.control.nombre -or $ficha.tipo -cne $q.control.tipo -or
        $ficha.padre -cne $q.control.padre -or !(Activa)) { return @{error='control'} }
    if ($q.foco -and ![Windows.Automation.Automation]::Compare($objetivo,$ae::FocusedElement)) { return @{error='foco'} }
    if ($q.operacion -eq 'clic') {
        if (!$ficha.click -or $ficha.click -cne $q.control.click) { return @{error='control'} }
        Activar $objetivo $ficha.click
        return @{estado='clic'}
    }
    if ($q.operacion -eq 'word') {
        if ($ficha.word -and $q.foco) { return @{estado='word'} }
        return @{error='foco'}
    }
    if (!$ficha.edita) { return @{error='control'} }
    $vp = Patron $objetivo ([Windows.Automation.ValuePattern]::Pattern)
    $esBusqueda = $q.operacion -eq 'buscar'
    $valor = if ($esBusqueda) { $q.texto } else { $vp.Current.Value + $q.texto }
    if (!(Activa)) { return @{error='ventana'} }
    $vp.SetValue($valor)
    if (!$esBusqueda) { return @{estado='escrito'} }
    # Un botón de búsqueda vecino se activa por identidad, nunca con Enter global.
    if ($boton -and (Activa)) {
        try {
            $b = Detalle $boton
            if ($b -and $b.click -eq 'Invoke' -and $b.nombre -ceq $q.boton.nombre -and
                $b.padre -ceq $ficha.padre -and (Activa)) {
                Activar $boton 'Invoke'
                return @{estado='buscado'}
            }
        } catch { return @{estado='consulta'} }
    }
    return @{estado='consulta'}
}

try { $respuesta = Ejecutar } catch { $respuesta = @{error='inaccesible'} }
[Console]::WriteLine(($respuesta | ConvertTo-Json -Compress -Depth 5))
