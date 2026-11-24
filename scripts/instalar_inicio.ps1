# Registra la tarea "JarvisLocal": arranca al iniciar sesión, al desbloquear y al volver de suspensión.
# Si ya está corriendo, la nueva instancia solo le avisa que salude y se cierra.
$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
$Pyw = Join-Path $Raiz ".venv\Scripts\pythonw.exe"
$Usuario = "$env:USERDOMAIN\$env:USERNAME"

# Eventos de "volví de suspensión": S3 clásico (Power-Troubleshooter 1) y Modern Standby (Kernel-Power 507).
$consulta = @"
<QueryList><Query Id="0" Path="System">
<Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]</Select>
<Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=507]]</Select>
</Query></QueryList>
"@
$consultaEsc = [System.Security.SecurityElement]::Escape($consulta)

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Jarvis local: asistente de voz de pendientes</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>$Usuario</UserId><Delay>PT5S</Delay></LogonTrigger>
    <SessionStateChangeTrigger><Enabled>true</Enabled><StateChange>SessionUnlock</StateChange><UserId>$Usuario</UserId></SessionStateChangeTrigger>
    <EventTrigger><Enabled>true</Enabled><Subscription>$consultaEsc</Subscription><Delay>PT3S</Delay></EventTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>$Usuario</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <StartWhenAvailable>true</StartWhenAvailable>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>$Pyw</Command><Arguments>-m asistente run</Arguments><WorkingDirectory>$Raiz</WorkingDirectory></Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName "JarvisLocal" -Xml $xml -Force | Out-Null
Write-Host "Tarea 'JarvisLocal' registrada (inicio de sesión, desbloqueo y reanudación)."
