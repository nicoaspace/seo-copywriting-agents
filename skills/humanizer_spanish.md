---
name: humanizer-spanish
version: 3.0.0
description: |
  Capa de humanización (filtro de estilo posterior) para textos en español producidos por la
  pipeline de copywriting. Detecta patrones evidentes de redacción IA. NO compite con el
  Copywriter Skill: si una regla de aquí choca con SKILL.md, references/, voz del Brand DNA,
  un requisito SEO, una fórmula de copywriting, una estructura de página o las excepciones
  reconocidas, gana siempre el Copywriter Skill.
---

# Humanizador (ES) — Capa de revisión estilística

## Prioridad y alcance

**El Copywriter Skill manda siempre.** Estas reglas son una **capa secundaria** que solo elimina señales evidentes de redacción IA en aspectos que el copywriter skill no regula. Si hay conflicto:

- SKILL.md, references/, voz del Brand DNA, requisitos SEO, fórmulas de copywriting (AIDA, PAS, PASO, etc.), estructura de página y excepciones reconocidas → **prevalecen**.
- Estas reglas → ceden.

Aplica los patrones a nivel de frase y párrafo durante la redacción y como pase final antes de entregar. El objetivo es que el texto suene escrito por una persona experta del sector, no diluir la voz de marca ni romper la estructura de copywriting.

---

## Patrones de contenido a evitar

### 1. Inflación de significado, legado y tendencias

Frases vigía: *supone/representa un hito, marca un momento decisivo/clave, pone de manifiesto, refleja la creciente, simboliza el compromiso con, contribuye a la construcción de, sienta las bases para, representa un punto de inflexión, paisaje en constante evolución, eje fundamental, deja una huella imborrable, profundamente arraigado.*

**Antes:** La creación del Instituto en 1945 marcó un hito decisivo en la evolución de la gestión de datos.
**Después:** El Instituto se creó en 1945 para centralizar la recopilación de datos.

### 2. Énfasis excesivo en notoriedad y cobertura

Frases vigía: *cobertura independiente, medios nacionales/regionales, escrito por un experto reconocido, amplia presencia en redes sociales.*

**Antes:** Sus ideas han sido citadas en El País, El Mundo y BBC Mundo.
**Después:** En una entrevista en El País (2023), argumentó que la regulación debe centrarse en resultados.

### 3. Gerundios apendiculares (relleno con -ndo)

Frases vigía: *destacando, subrayando, enfatizando, garantizando, reflejando, simbolizando, contribuyendo a, potenciando, impulsando, fomentando, abarcando, evidenciando.*

**Antes:** El edificio usa azul, verde y dorado, simbolizando el mar y reflejando la conexión con el entorno.
**Después:** El edificio usa azul, verde y dorado en referencia al Mediterráneo y a la sierra interior.

### 4. Atribuciones vagas

Frases vigía: *informes del sector, observadores han señalado, los expertos argumentan, algunos críticos sostienen, diversas fuentes/publicaciones.*

**Antes:** Los expertos coinciden en que juega un papel fundamental.
**Después:** Según un estudio del CSIC (2021), alberga varias especies endémicas.

### 5. Secciones formulaicas "Retos y perspectivas"

Frases vigía: *A pesar de…, enfrenta diversos desafíos…, A pesar de estos retos, Retos y perspectivas, Perspectivas de futuro, Desafíos y oportunidades.*

Sustituye por hechos concretos con datos y fechas.

---

## Patrones lingüísticos y gramaticales

### 6. Vocabulario IA acumulado

- **Conectores en exceso:** asimismo, no obstante, por otro lado, por otra parte, de hecho, en consecuencia, así pues, por ende, en definitiva, en síntesis, en efecto, por lo tanto (acumulados).
- **Intros de importancia:** *cabe destacar, cabe mencionar, cabe señalar, es importante destacar que, es importante mencionar que, es fundamental señalar que, hay que tener en cuenta que.*
- **Frases de escenario:** *en el panorama actual, en el mundo actual, en el contexto actual, en la actualidad, en el dinámico mundo de, en el cambiante entorno de.*
- **Profundidad falsa:** *en esencia, en el fondo, en última instancia, en su núcleo, a nivel profundo, en su raíz.*
- **Cierres formulaicos:** *en conclusión, en resumen, en definitiva (cuando cierra artificialmente), para concluir, como conclusión.*

**Antes:** Cabe destacar que, en el panorama actual, la digitalización juega un papel fundamental. Asimismo, es importante señalar que afecta también a las pymes. En definitiva, las que no se adapten se quedarán atrás.
**Después:** La digitalización ya no es opcional para las pymes. Las que siguen facturando en hojas de cálculo no están "siendo cautelosas": están acumulando un retraso que cuesta el doble recuperar.

### 7. Evasión copulativa (evitar "ser/estar")

Frases vigía: *se erige como, se presenta como, se convierte en, actúa como, funciona como, opera como, se configura como, se posiciona como.*

**Antes:** La cooperativa se erige como referente. El mercado actúa como punto de encuentro y funciona como motor económico.
**Después:** La cooperativa es referente del comercio justo. El mercado es el punto de encuentro y el principal motor económico.

### 8. Nominalizaciones burocráticas

Frases vigía: *la realización de, la implementación de, el desarrollo de, la elaboración de, la gestión de, la prestación de, la consecución de, la optimización de, llevar a cabo la…, proceder a la…, hacer uso de.*

Prefiere verbos activos. **Excepción:** si el Brand DNA define un registro B2B/sectorial donde estos términos son la voz natural del cliente (legal, contable, ingeniería, sector público), respeta el Brand DNA.

**Antes:** La empresa procedió a la elaboración de un protocolo para la gestión de incidencias.
**Después:** La empresa elaboró un protocolo para gestionar incidencias.

### 9. Paralelismos negativos y negaciones apendiculares

Patrón: *No solo X, sino Y / No se trata solo de X, sino de Y.* También coletillas tipo *"sin necesidad de adivinanzas"* pegadas al final de una frase.

**Excepción CTA:** "sin permanencia / sin compromiso / sin tarjeta de crédito / sin coste / sin letra pequeña" son ganchos legítimos en pricing/landing/sales y NO deben marcarse.

**Antes:** No se trata solo del precio; se trata de la confianza. No es únicamente una herramienta, es un cambio de mentalidad.
**Después:** El precio importa, pero la confianza importa más.

### 10. Variación elegante (sinonimia cíclica excesiva)

Sustituir 4 sinónimos por el mismo concepto en 4 frases seguidas: *protagonista → heroína → joven → muchacha*.

**Excepción SEO:** la variación semántica moderada (2-3 formas semánticas distintas en un artículo largo) está permitida y favorece el ranking.

**Antes:** La protagonista enfrenta obstáculos. La heroína debe superar retos. La joven vence dificultades. La muchacha triunfa.
**Después:** La protagonista enfrenta muchos obstáculos pero al final triunfa.

### 11. Falsas gradaciones

Patrón: *desde X hasta Y* donde X e Y no forman una escala real.

**Antes:** Nuestro recorrido nos ha llevado desde las primeras civilizaciones hasta los algoritmos de IA, desde el trueque hasta los mercados financieros.
**Después:** El libro cubre la historia del comercio: del trueque a los mercados digitales.

### 12. Voz pasiva y fragmentos sin sujeto

**Antes:** No se requiere ningún archivo. Los resultados quedan preservados automáticamente.
**Después:** No necesitas ningún archivo. El sistema guarda los resultados automáticamente.

### 13. Abuso de "el mismo / la misma" anafórico

Es propio del español jurídico-administrativo. En texto natural se usa pronombre o se reorganiza la frase.

**Antes:** Presentó su informe. El mismo fue bien recibido por los inversores, quienes destacaron los datos del mismo.
**Después:** Presentó su informe. Los inversores lo recibieron bien y destacaron sus datos.

### 14. Conectores formales acumulados

Encadenar *asimismo / no obstante / sin embargo / por otra parte / en consecuencia* en párrafos cortos. Reduce a uno por párrafo o elimínalos cuando la frase ya implica la relación lógica.

### 15. Hedging excesivo

Patrón: *podría potencialmente posiblemente, quizás eventualmente, en cierta medida tal vez.*

**Excepción YMYL:** en contenido legal, médico, financiero, fiscal o sanitario, el hedging es **obligatorio** por compliance. NO marques "puede", "es recomendable consultar", "según la normativa vigente" en estos contextos.

**Antes:** Podría potencialmente argumentarse que la política tal vez tendría algún efecto.
**Después:** La política puede afectar los resultados.

---

## Patrones de estilo

### 16. Em dashes en exceso

Más de 2 guiones largos cada 300 palabras suele indicar IA. Sustituye por comas, puntos o paréntesis.

**Antes:** El término lo usan las instituciones —no la gente—y persiste —incluso en documentos oficiales—.
**Después:** El término lo usan las instituciones, no la gente, y persiste incluso en documentos oficiales.

### 17. Negritas mecánicas en frases sueltas

Marcar en negrita conceptos sin propósito jerárquico real. Limítate a negritas con función estructural.

**Excepción estructural:** los listados con encabezado en negrita (`- **Encabezado:** texto`) están permitidos en listicles, FAQ pages, service pages y secciones tipo comparativa cuando la `references/` correspondiente lo prescribe. NO marques este patrón en esos contextos.

### 18. Title Case al estilo inglés en títulos en español

En español solo va en mayúscula la primera palabra y los nombres propios.

**Antes:** ## Estrategias De Negociación Y Alianzas Globales
**Después:** ## Estrategias de negociación y alianzas globales

### 19. Emojis decorativos

No decores encabezados ni viñetas con emojis salvo que el Brand DNA lo prescriba explícitamente.

### 20. Anuncios meta ("veamos…")

Frases vigía: *Veamos en qué consiste, exploremos, analicemos juntos, esto es lo que necesitas saber, ahora veamos, sin más preámbulos, a continuación veremos.*

La IA anuncia lo que va a hacer en lugar de hacerlo. Empieza directamente.

### 21. Encabezados fragmentados

Un H2/H3 seguido de una frase de una sola línea que repite el encabezado con otras palabras. Si la primera frase no aporta información nueva, elimínala.

### 22. Frases de relleno

Sustituciones rápidas:
- "en orden a / con el objetivo de / con el fin de" → "para"
- "debido al hecho de que" → "porque"
- "en este momento del tiempo" → "ahora"
- "en el caso de que" → "si"
- "tiene la capacidad de" → "puede"
- "es importante señalar que los datos muestran" → "los datos muestran"

### 23. Conclusiones positivas genéricas

Frases vigía: *El futuro es prometedor, se vislumbran tiempos emocionantes, marca un avance significativo, abre nuevas posibilidades.*

Cierra con un hecho específico, una recomendación accionable o un CTA real, no con un brindis genérico.

### 24. Artefactos de chat

Frases vigía: *Espero que esto ayude, Por supuesto, Claro que sí, ¿Te gustaría que…?, Aquí tienes…, Déjame saber si…*

No deben aparecer nunca en contenido publicado.

### 25. Disclaimers de modelo / fecha de corte

Frases vigía: *Hasta mi última actualización, según la información disponible, aunque los detalles específicos son escasos, no tengo acceso a datos en tiempo real.*

Fuera del texto final.

### 26. Tono servil / sicofántico

*¡Excelente pregunta!, Tienes toda la razón, Es un punto fascinante.*

Fuera.

---

## Excepciones reconocidas (NO marcar como AI pattern)

Antes de flaggear cualquier patrón, verifica que no caiga en una de estas excepciones. Si cae, no es un fallo:

1. **Vocabulario persuasivo en sales/landing/pricing/product** — palabras como "innovador, vibrante, líder, único, excepcional, en el corazón de" son aceptables cuando el Brand DNA las respalda y la página tiene función comercial. Solo márcalas si el Brand DNA define explícitamente "Avoided Terms" que las incluyan, o si entran en la categoría "Ethical Claims" (superlativos no verificables, claims sin prueba).
2. **Listas estructurales con encabezado en negrita** (`- **Concepto:** explicación`) en listicles, FAQ pages, service pages y secciones comparativas, cuando la `references/` del page_type lo prescribe.
3. **Hedging legalmente requerido** en contenido YMYL (legal, médico, financiero, fiscal, sanitario).
4. **Triadas dentro de fórmulas de copywriting** (AIDA, PAS, headlines de tres partes, "más rápido, más simple, más barato"). La regla de tres es legítima cuando es estructural a la fórmula.
5. **CTAs de conversión** con coletillas "sin permanencia / sin compromiso / sin tarjeta / sin coste / sin letra pequeña".
6. **Terminología técnica B2B / sectorial** prescrita por el Brand DNA: "implementación", "gestión integral", "prestación de servicios" cuando el cliente y el sector hablan así.
7. **Variación semántica moderada** (2-3 formas distintas para el mismo concepto a lo largo de un artículo) que apoya SEO.
8. **Repetición controlada del keyword principal** en H1, primeros 100 palabras y meta — es requisito SEO.
