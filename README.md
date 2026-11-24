# Jarvis local

Asistente de voz que corre en tu laptop. Puedes pedirle tareas por voz, ajustar cómo habla y revisar en pantalla lo que quieres encargar a ChatGPT o Claude. El saludo y el resumen de la mañana se pueden configurar.

- **Local:** el modelo (Ollama), el reconocimiento de voz (Whisper) y la voz (Piper) corren en tu PC.
- **Rápido:** las órdenes se entienden con reglas, sin pasar por el modelo. El modelo solo se usa para conversar, redactar y resumir.

## Instalación y conexiones

1. En Windows 10 u 11, clona este repositorio y abre **`instalar.bat`**. El instalador prepara el entorno y descarga los modelos. Después de actualizar, ejecuta **`reparar.bat`**.
2. **`conectar-clementina.bat`**: pega el enlace de *Calendario → Exportar → Obtener URL del calendario*.
3. **Google (Gmail + Calendar):**
   1. En Google Cloud, proyecto Jarvis → *APIs y servicios → Biblioteca*: habilita **Gmail API** y **Google Calendar API**.
   2. Crea un cliente OAuth de tipo **Aplicación de escritorio**. Guarda el JSON descargado como `credentials.json` junto a `instalar.bat`. [Guía de Google](https://developers.google.com/workspace/gmail/api/quickstart/python?hl=es-419)
   3. Ejecuta **`conectar-google.bat`** y acepta los permisos (Gmail solo lectura y eventos de Calendar).
4. **WhatsApp:** **`conectar-whatsapp.bat`** (o di "Jarvis, vincula WhatsApp"). Se abre WhatsApp Web en el navegador de Jarvis: escanea el código desde tu celular en *WhatsApp → Dispositivos vinculados*.
5. **Plataformas:** la primera vez di "entra a Clementina", inicia sesión tú en la ventana de Jarvis y acepta **Guardar contraseña** en Chrome. Las siguientes veces Jarvis presiona "Entrar" solo.
6. **`iniciar.bat`** para probar con consola visible. Desde el próximo inicio de sesión arranca solo.
7. **`configurar.bat`** abre el panel de Jarvis incluso con la esfera oculta. Mientras Jarvis está activo, también puedes abrirlo con un clic derecho en la esfera o desde el icono de la bandeja.

Personaliza `config.toml` en tu equipo. La plantilla versionada es `config.ejemplo.toml`. La configuración personal, las credenciales y los datos de uso se guardan localmente.

### El navegador de Jarvis

Jarvis usa un perfil propio de Chrome para WhatsApp Web y las plataformas. Las sesiones se guardan en `datos\navegador-jarvis`. Las búsquedas y YouTube se abren en el navegador configurado. Para los encargos de ChatGPT y Claude, Jarvis usa las aplicaciones de escritorio de Windows.

- La contraseña **nunca** la escucha ni la escribe Jarvis: Chrome la autocompleta, o la escribes tú.
- WhatsApp: **nunca envía sin tu "sí"**. Deja el mensaje escrito, te lo lee y pregunta "¿Lo envío?".

## Qué le puedes decir

«Jarvis, abre Word» da la orden en una frase. En **Escucha** puedes activar la confirmación de aperturas. Con esa opción, si ya estás conversando y dices «abre YouTube» sin llamarlo, Jarvis pregunta «¿Abro YouTube?». Un «sí» confirma esa apertura durante 15 segundos.

«Quiero que me digas Fernando» cambia cómo se dirige a ti. Jarvis guarda el nombre en esta computadora para el próximo inicio.

Si Whisper oye «World» en «abre World», Jarvis busca el Word instalado. En «busca world music», conserva las palabras de la búsqueda. También recuerda el servicio durante una aclaración. Si dices «ChatGPT» y te pregunta qué quieres hacer allí, puedes contestar «ábrelo».

### Trabajar en la ventana activa

| Para | Puedes decir |
|---|---|
| Identificar la ventana y sus controles visibles | «Jarvis, ¿qué ves en la pantalla?» |
| Activar un botón o enlace por nombre | «Jarvis, haz clic en Buscar» |
| Escribir en el campo activo | «Jarvis, escribe aquí reunión del viernes» |
| Elegir un campo | «Jarvis, escribe Fernando en el campo Nombre» |
| Usar el buscador de esa ventana | «Jarvis, busca aquí presupuesto 2026» |

Jarvis consulta la accesibilidad de Windows en el momento del pedido. La descripción depende de los nombres y controles que expone cada aplicación. Si cambias de ventana durante una confirmación, vuelve a pedir la acción en la ventana de destino.

### Otras órdenes

| Tema | Ejemplos |
|---|---|
| Tu día | "Resumen de la mañana", "¿cómo está mi día?", "¿qué pendientes tengo?", "¿qué tengo para hoy?" |
| Calendar | "Agenda reunión con el asesor mañana a las 4", "agenda el viernes sustentación de 3 a 5 de la tarde", "crea un evento llamado cumpleaños de Ana el sábado" (todo el día), "¿qué tengo en mi calendario mañana?" |
| WhatsApp | "Mándale un WhatsApp a Juan diciendo que llego en 10 minutos", "dile a mamá por WhatsApp que ya salí", "lee los mensajes de Juan", "¿tengo mensajes nuevos en WhatsApp?", "busca en WhatsApp tarea del domingo" |
| Plataformas | "Entra a Clementina" (inicia sesión). Agrega más en `config.toml` → `[plataformas]` |
| Archivos | "Busca el archivo tesis", "¿dónde está la foto carnet?", "abre el documento informe final", "encuentra el PDF ficha". Te pregunta "¿Lo abro?"; puedes decir "el segundo" o "abre la carpeta" |
| Resumir | Con el documento al frente: "resume este documento", "resume lo que tengo abierto", "resume el archivo informe final". Te lee el resumen, ofrece los puntos clave y lo guarda en Documentos\Jarvis\Resúmenes |
| Selección | Con texto seleccionado: "léeme esto", "traduce esto al inglés", "explícame esto" |
| Correos | "Léeme el último correo", "¿qué dice el correo de la UCV?", "abre el correo de Amazon" |
| Navegador | "Abre Chrome con el perfil Trabajo", "busca vuelos a Cusco", "busca con IA…", "pon música lofi en YouTube", "pregúntale a ChatGPT / Claude / Gemini…" |
| Documentos | "Hazme un Word / PDF / presentación / Excel sobre…", "llena la plantilla de solicitud" |
| Recordatorios | "Recuérdame en 20 minutos…", "pon una alarma a las 6", "temporizador de 10 minutos", "modo estudio de 50 minutos" |
| Sistema | "Abre Word / Spotify / la carpeta de descargas", "sube el volumen", "pausa", "toma una captura", "bloquea la computadora", "modo dictado" |
| Voz | "Repite", "habla más despacio / más rápido / normal", "descansa", "apágate", "nada" o "trabajaré solo" (silencio hasta que digas "Jarvis…"). "Ciérrate" o "apágate del todo" cierra Jarvis |
| Interrumpir | Háblale encima mientras habla: se calla y te escucha. "Ya", "para", "basta" solo lo callan; también puedes pedirle otra cosa. Clic en la esfera = cállate |
| Cancelar | "Ya no quiero que…", "no lo envíes", "olvídalo", "cancela", "mejor no": suelta lo que estaba haciendo o preguntando |
| Tu laptop | "¿Cuánta memoria estoy usando?", "¿qué está consumiendo memoria?", "¿por qué vas lento?", "¿cómo está el procesador?", "¿cuánta batería me queda?" |
| Ventanas | "Abre el navegador / Word" (si ya está abierto lo trae al frente), "cambia a YouTube", "ve a Word", "minimiza Word", "maximiza esto", "cierra esta ventana", "cierra Spotify", "muestra el escritorio" |
| Teclado | "Nueva pestaña", "cierra la pestaña", "recarga la página", "atrás", "copia", "pega", "deshaz", "guarda", "selecciona todo", "dale enter", "presiona control más t", "presiona alt f4", "baja", "sube", "escribe hola, ¿cómo estás?" |
| Sugerencias durante la conversación | «¿En qué avanzo?» o «¿Qué entregas tengo?». Si hay una actividad próxima en la agenda actualizada, Jarvis puede sugerir un plan de estudio o un esquema de entrega. Al aceptar se guarda un Word local. Puedes desactivar las sugerencias en **Escucha**. |
| Energía | "Suspende la computadora", "hiberna la computadora", "apaga la pantalla", "cierra sesión" (pregunta), "apaga / reinicia la computadora" (pregunta) |
| Si te entiende mal | Si una frase no calza con ninguna orden pero parece un pedido ("te abre el naveador", "no veo que hayas abierto el navegador", "no la app, en el navegador"), el modelo la traduce a la orden correcta antes de responder |

**Para probar sin micrófono:** `consola.bat`.

## Proyectos locales de Word y PowerPoint

Para trabajar directamente con Ollama y Office puedes usar **Proyectos**, también disponible desde `configurar.bat`. Elige un Word guardado o déjalo al frente y haz el pedido por voz. Las copias nuevas se guardan junto al archivo de origen. Jarvis las vuelve a leer y comprueba qué archivo abrió Office.

| Durante el trabajo | Ejemplo |
|---|---|
| Resumir un Word | «Resume esto en una copia, conserva las tablas y muéstrame qué cambiaste». La copia incorpora un resumen y conserva el desarrollo. |
| Ajustar una revisión | «Espera, conserva la tabla original y cambia solo el resumen». «Muéstrame los cambios» enseña el detalle en Proyectos. |
| Preparar una exposición | «Convierte este informe en seis diapositivas y comprueba que estén las conclusiones». La portada cuenta en el total. Si el documento carece de una sección de conclusiones, el cierre usa extractos del texto. |
| Corregir una diapositiva | «La tercera tiene demasiado texto» o «acorta esta diapositiva». Jarvis crea otra copia de la presentación. |
| Revisar la duración | Con la presentación al frente, «tengo diez minutos para esta presentación». Si conviene reducirla, Jarvis indica la cantidad sugerida y espera tu respuesta. El tiempo por diapositiva es orientativo. |
| Pausar y retomar | «Pausa el trabajo» y «reanuda el trabajo». Para volver al archivo previo, «vuelve a la versión anterior». Las copias siguen en disco. |
| Recordar un proyecto | «Trabajemos en el proyecto Tesis», «anota como decisión usar entrevistas» y «queda pendiente revisar las citas». Otro día, «retomemos la tesis» recupera la versión de trabajo y tus apuntes. |
| Consultar tus notas locales | «Vincula mis notas sobre entrevistas a este proyecto», «consulta las notas del proyecto» y «marca como hecho revisar las citas». Jarvis vuelve a leer el Markdown cuando se lo pides. |

### Rutinas personales

«Jarvis, prepara mi sesión de tesis» usa el proyecto **Tesis**. La rutina abre su ficha de Obsidian y el Word verificado de la versión de trabajo, y lee el primer pendiente. Puedes crear el proyecto con «trabajemos en el proyecto Tesis». Si todavía no tiene un Word, pon el documento guardado al frente cuando la rutina lo indique, di «vincula este Word a la rutina» y después «reanuda la rutina».

Para enseñar otra secuencia, di «crea la rutina Lectura para el proyecto Tesis». Responde, por ejemplo, «abre la ficha y dime el primer pendiente» y confirma el orden. Di «ejecuta la rutina Lectura» para usarla o «qué rutinas tengo» para consultar sus nombres. Si un paso queda pendiente, «reanuda la rutina» continúa desde ahí, incluso tras reiniciar Jarvis. «Omite este paso de la rutina» permite seguir con el siguiente.

## Encargos con ChatGPT y Claude

Abre **Encargos** en el panel o di «Jarvis, usa Claude para revisar un documento de Word». Puedes elegir entre un informe a partir de notas, una copia revisada de un Word, una presentación a partir de un documento y un encargo de texto libre.

1. Elige la aplicación, describe el trabajo y selecciona las fuentes. Para revisar un Word, elige un único `.docx`. Los informes y presentaciones admiten fuentes de Word, texto y Markdown.
2. Pulsa **Revisar encargo**. Verás el destino, los nombres de los archivos y el texto completo preparado a partir de ellos.
3. **Autorizar y enviar** entrega ese texto al cuadro de mensaje de la aplicación. Si Windows no expone un cuadro vacío y un botón de envío identificables, revisa el aviso. **Copiar encargo** permite continuar manualmente.
4. Para generar el archivo, selecciona la respuesta completa en la aplicación. En **Ya tengo la respuesta**, impórtala o pégala y pulsa **Guardar archivo nuevo**. La revisión de Word conserva el original. Si el original cambió durante el encargo, vuelve a preparar la revisión.

## El widget (la esfera)

Al arrancar aparece una esfera de partículas en la esquina inferior derecha, siempre encima de las demás ventanas y sin quitarte el foco. Te muestra qué está haciendo Jarvis:

| Esfera | Significa |
|---|---|
| Gira despacio, blanca | Te está escuchando |
| El borde vibra, celeste | Te está oyendo hablar |
| Se arremolina, violeta | Está pensando |
| Late con la voz, blanca brillante | Está hablando |
| Tenue | En silencio: solo responde si dices "Jarvis…" |

- **Subtítulos:** al lado aparece lo que entendió que dijiste y lo que responde. Así ves si te escuchó mal.
- **Clic:** si está hablando, se calla; si no, "te escucho". **Clic derecho:** silenciar micrófono, ocultar subtítulos, salir. **Arrastrar:** moverla (recuerda la posición).
- En **Widget** puedes previsualizar la esfera, elegir un tamaño de 120 a 320 px y mostrar u ocultar los subtítulos. Al guardar, la esfera cambia de tamaño en el escritorio. Su menú contextual también permite agrandarla o reducirla.

## Voz y velocidad

En **Configuración → Voz**, pulsa **Usar frase de acentos** y escucha la misma frase con las opciones A y B. Elige la que mejor distinga «práctico», «practico» y «practicó», y pulsa **Guardar cambios**. En **Pronunciaciones personales** puedes indicar cómo leer un apellido o una sigla. El ajuste se aplica al hablar y conserva la escritura de tus documentos.

Para enseñar un nombre de aplicación, di «Jarvis, cuando te pida abrir World, me refiero a Microsoft Word». Puedes revisar y eliminar lo aprendido en **Configuración → Escucha**. «Escribe: World» conserva la palabra dictada.

En **Escucha → Ensayo con tu voz** puedes leer doce frases y comparar cada una con lo que transcribió Jarvis. Durante el ensayo se suspenden las órdenes de voz. Los resultados muestran las palabras, las tildes y el tiempo de reconocimiento. Pulsa **Terminar ensayo** para volver a dar órdenes.

En el menú de la esfera, **Última acción** muestra la petición, la interpretación y el resultado. También puedes decir «¿por qué abriste eso?». Al abrir la portada de YouTube o Google en Chrome o Edge, Jarvis intenta identificar la pestaña. Si el recibo ofrece «deshazlo», puedes cerrar esa pestaña mientras conserva la dirección original y está seleccionada. El recibo dura hasta la siguiente acción o el reinicio de Jarvis.

- La respuesta se escucha frase por frase. Las muletillas están desactivadas de fábrica.
- **Interrumpir:** mientras habla sigue escuchando y compara lo que oye con lo que sale por el parlante; si le
  hablas encima con claridad, se calla. Si se corta solo (su propia voz), sube `[escucha] sensibilidad_interrupcion`
  (3 por defecto; con audífonos puedes bajarla a 2). Para desactivarlo: `interrumpir = false`.
- **Memoria (RAM):** si está casi llena, Windows usa el disco y todo se traba, incluida la voz. Jarvis te avisa y
  te dice qué programa ocupa más ("¿qué está consumiendo memoria?").
- En `[voz]`, el ritmo inicial es `velocidad = 1.18`, la pausa entre frases es de 95 ms y `tono_semitonos = 0.7` eleva ligeramente la voz. Puedes decir «habla más despacio» o «habla normal» para ajustar el ritmo. `relleno = true` activa las muletillas.
- `[stt] modelo = "small"` usa el modelo local de reconocimiento en español. `diagnostico.bat` permite comparar una frase sintética con una grabación de tu micrófono.
- **`diagnostico.bat`** mide la velocidad real del modelo, la voz y tu micrófono (`datos\diagnostico.txt`).
- **`optimizar.bat`** prueba combinaciones en tu laptop y se queda con la más rápida que entienda bien:
  graba 2 frases con tu voz, compara Whisper small/base con distinto número de hilos, descarga y prueba
  los modelos de `[llm] candidatos` (por defecto `qwen3.5:2b-q4_K_M` y `qwen3:1.7b`) en una charla corta,
  y te pregunta antes de guardar (deja copia en `config.toml.bak`). Detalle en `datos\optimizar.txt`.
  Hazlo con el cargador conectado y el modo de energía en "Máximo rendimiento".

## Plantillas para llenar

En **Documentos\Jarvis\Plantillas**: Word o Excel con campos `{{nombre completo}}`, o PDF rellenables. Di "llena la plantilla de …" y responde cada campo.

## Configuración (`config.toml`)

### Conocimiento que se conserva al reiniciar

Di «recuerda que el proyecto Faro usa SQLite» y más tarde «¿qué recuerdas sobre el proyecto Faro?». También puedes decir «guarda tu última respuesta en Obsidian» o «busca en mis notas proyecto Faro». Jarvis devuelve extractos de las notas y recoge sus ediciones en la siguiente consulta.

El primer guardado crea la carpeta `Conocimiento` dentro de la carpeta de documentos de Jarvis. «¿Dónde está mi base de conocimiento?» indica la ruta. Para trabajar con Obsidian, elige esa carpeta al **abrir una carpeta como bóveda**. Para usar otra bóveda existente, configura `carpeta` en la sección `[conocimiento]` y reinicia Jarvis. La búsqueda también consulta las notas rápidas anteriores de Jarvis. [Formato de las bóvedas de Obsidian](https://obsidian.md/help/data-storage)

Las memorias nuevas incluyen fecha, tipo y etiquetas. Si hay un proyecto activo, llevan un enlace a su ficha. El panel **Sistema** muestra la ruta que usa Jarvis.

| Qué quieres hacer | Qué puedes decir |
|---|---|
| Retomar un proyecto | «Trabajemos en el proyecto Tesis». «Queda pendiente revisar las citas». Puedes marcar la casilla directamente en `Proyectos/Tesis.md`. «Retomemos la tesis» recoge la edición. |
| Abrir una ficha en Obsidian | «Abre la nota de la tesis en Obsidian». |
| Consultar el diario | «¿Qué hicimos hoy?» o «¿qué hicimos ayer?». Las notas de `Diario/AAAA-MM-DD.md` registran las memorias guardadas, decisiones, tareas y versiones a partir de la conexión. |
| Comparar opciones | «Ayúdame a decidir entre entrevistas y encuestas para la tesis». Jarvis cita los extractos relevantes y sus líneas. Si hay evidencia suficiente, Ollama propone una opción. Puedes aceptarla o decir «he decidido…» con tu propia elección. |

### Investigar en la bóveda

Di «Jarvis, investiga en mis notas el presupuesto del viaje». Jarvis combina palabras clave y similitud de significado para localizar pasajes como «costos de pasajes y alojamiento». Lee extractos con su fuente. Con «compara lo que dicen mis notas sobre la fecha de entrega» puedes contrastar dos documentos.

En el mismo intercambio puedes decir «muéstrame las fuentes», «repite la investigación» o «guarda el informe». Al guardar se crea una nota Markdown en `Informes` con enlaces y líneas de origen. Si alguien edita el pasaje entre la consulta y el guardado, Jarvis pide actualizar la investigación.

La opción **Sistema → Buscar notas también por su significado** utiliza el modelo de recuperación configurado en Ollama local. Para habilitarla en otra instalación, descarga `embeddinggemma:300m-qat-q4_0` con `ollama pull embeddinggemma:300m-qat-q4_0`, activa la opción y reinicia Jarvis. La búsqueda por palabras sigue disponible si Ollama está desconectado. En bóvedas grandes, la comparación semántica puede completarse en varias consultas. El panel muestra el estado del modelo.

### Control de Windows y permisos

| Pedido de ejemplo | Resultado |
|---|---|
| «Minimiza todas las aplicaciones, menos tú» | Minimiza las ventanas elegibles y comprueba cuántas quedaron minimizadas. |
| «Abre una nueva ventana del navegador» | Solicita otra ventana y comprueba que Windows la haya creado. |
| «Sube todo el volumen al 100 %» o «pon el brillo al 60 %» | Solicita ese nivel y lee el valor que devuelve el dispositivo. El brillo depende del monitor. |
| «¿Qué permisos tienes?» | Informa de los permisos actuales del proceso. |
| «Abre Servicios como administrador» | Solicita elevación para la consola de Servicios. También admite Visor de eventos, Administración de equipos y los administradores de dispositivos y de tareas. |
| «Verifica los archivos del sistema» | Solicita a Windows la comprobación con `sfc /verifyonly` y comunica su finalización. |
| «Vacía la caché DNS» | Solicita la limpieza de la caché de resolución y comprueba el código de salida. |

Jarvis funciona en tu sesión normal. Cuando una operación requiere elevación, Windows presenta el aviso UAC conforme a la configuración del equipo. La autorización se da en el diálogo de Windows. [Control de cuentas de usuario de Windows](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/user-account-control/how-it-works)

### Otras opciones

- `[escucha] confirmar_aperturas = true` vuelve al modo cauteloso: si no empiezas con "Jarvis", pregunta "¿Abro X?" antes de abrir algo. Por defecto actúa directo.
- `[preferencias] whatsapp = "app"` abre la aplicación de WhatsApp en vez de WhatsApp Web.

- `[escucha] resumen_matutino`: resumen del día en el primer saludo de la mañana.
- `[plataformas]`: nombre = dirección, para decir "entra a <nombre>".
- `[archivos] carpetas_extra`: carpetas adicionales donde buscar archivos.
- `[navegador] perfil_predeterminado`, `[sitios]`, `[documentos] modelo`, `[clima]`, `[llm] modelo`, `[stt] modelo`.
- Los tiempos reales quedan en `datos\asistente.log` (líneas con ⏱).

## Si algo falla

- **WhatsApp o una plataforma no responde como se espera:** WhatsApp Web cambia su diseño seguido. Jarvis guarda capturas del problema en `datos\debug`; con eso se ajusta.
- **"Falta el permiso de Google Calendar":** habilita la Calendar API y vuelve a ejecutar `conectar-google.bat`.
- **Google pide reconectar cada 7 días:** completa la *Información de la marca* y pulsa *Publicar app*.
- **No me escucha:** *Configuración → Privacidad → Micrófono*, permite apps de escritorio.
- **"Revisa que Ollama esté abierto":** abre Ollama desde el menú Inicio.
- **No resume un PDF:** si es escaneado (imagen), no tiene texto que leer.
- Quitar el arranque automático: `Unregister-ScheduledTask JarvisLocal` en PowerShell.
