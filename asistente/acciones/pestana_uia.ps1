$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern System.IntPtr GetForegroundWindow(); [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool IsWindow(System.IntPtr hwnd);' -Name Browser -Namespace Jarvis
$q = [Console]::In.ReadToEnd() | ConvertFrom-Json
$ae = [Windows.Automation.AutomationElement]
$ct = [Windows.Automation.ControlType]
$scope = [Windows.Automation.TreeScope]::Descendants
$walker = [Windows.Automation.TreeWalker]::RawViewWalker

function EsNativo($el) {
    while ($null -ne $el) {
        if ([Windows.Automation.Automation]::Compare($el, $raiz)) { return $true }
        $v = $el.Current
        if ($v.ControlType -eq $ct::Document -or $v.ClassName -in 'WebView','Chrome_RenderWidgetHostHWND') {
            return $false
        }
        $el = $walker.GetParent($el)
    }
    return $false
}
function Activa { [Jarvis.Browser]::GetForegroundWindow().ToInt64() -eq $q.hwnd }
function CoincideDireccion {
    if (!(Activa) -or !$barra.Current.IsEnabled -or $barra.Current.HasKeyboardFocus) { return $false }
    $valor = $barra.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
    return $q.urls -ccontains $valor
}
function Seleccionada($tab) {
    $p = $null
    return $tab.TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern, [ref]$p) -and $p.Current.IsSelected
}
function TipoCond($tipo) {
    return [Windows.Automation.PropertyCondition]::new($ae::ControlTypeProperty, $tipo)
}
function Examinar {
    if (!(Activa)) { return @{distinta=$true} }
    $raiz = $ae::FromHandle([IntPtr]$q.hwnd)
    if ($raiz.Current.ProcessId -ne $q.pid) { return @{distinta=$true} }
    $cond = [Windows.Automation.PropertyCondition]::new($ae::ClassNameProperty, 'OmniboxViewViews')
    $barras = @($raiz.FindAll($scope, $cond) | Where-Object { EsNativo $_ })
    if ($barras.Count -ne 1) { return @{distinta=$true} }
    $barra = $barras[0]
    if (!(CoincideDireccion)) { return @{distinta=$true} }
    $tabs = @($raiz.FindAll($scope, (TipoCond $ct::TabItem)) | Where-Object {
        $_.Current.ClassName -in 'Tab','EdgeTab' -and (EsNativo $_) -and (Seleccionada $_)
    })
    if ($tabs.Count -ne 1) { return @{distinta=$true} }
    $tab = $tabs[0]
    $id = $tab.GetRuntimeId() -join '.'
    if (!$id -or ($q.operacion -eq 'cerrar' -and $id -cne $q.id)) { return @{distinta=$true} }
    $botones = @($tab.FindAll($scope, (TipoCond $ct::Button)) | Where-Object {
        $_.Current.IsEnabled -and ($_.Current.ClassName -in 'TabCloseButton','CloseTabButton','EdgeTabCloseButton' -or
            $_.Current.Name -match '^(?:Cerrar(?: pestaña)?|Close(?: tab)?)$')
    })
    if ($botones.Count -ne 1) { return @{distinta=$true} }
    $invocar = $null
    if (!$botones[0].TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$invocar)) {
        return @{distinta=$true}
    }
    if (!(CoincideDireccion) -or !(Seleccionada $tab)) { return @{distinta=$true} }
    if ($q.operacion -eq 'identificar') { return @{id=$id} }
    if ($q.operacion -ne 'cerrar') { return @{distinta=$true} }
    $invocar.Invoke()
    for ($i=0; $i -lt 8; $i++) {
        Start-Sleep -Milliseconds 120
        if (![Jarvis.Browser]::IsWindow([IntPtr]$q.hwnd)) { return @{cerrada=$true} }
        $viva = @($raiz.FindAll($scope, (TipoCond $ct::TabItem)) | Where-Object {
            ($_.GetRuntimeId() -join '.') -ceq $id
        })
        if (!$viva.Count) { return @{cerrada=$true} }
    }
    return @{incierta=$true}
}
try { $res = Examinar } catch { $res = @{incierta=$true} }
[Console]::Write(($res | ConvertTo-Json -Compress))
