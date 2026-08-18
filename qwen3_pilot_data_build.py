#!/usr/bin/env python3
"""Genera el dataset pilot JSONL (català real, contingut educatiu general,
sense dades personals ni informació identificable).

Ús: python3 qwen3_pilot_data_build.py <data_dir>
"""
import json
import os
import sys

TRAIN = [
    # ── salut i biologia ──
    ("Què és la diabetis?", "La diabetis és una malaltia crònica en què el cos no produeix prou insulina o no la fa servir correctament, cosa que provoca un excés de glucosa a la sang."),
    ("Què és el colesterol?", "El colesterol és un lípid present a les cèl·lules del cos, necessari per formar membranes i hormones, però el seu excés a la sang pot afavorir malalties cardiovasculars."),
    ("Què és una vacuna?", "Una vacuna és una preparació que estimula el sistema immunitari perquè generi defenses contra un patogen, sense que la persona desenvolupi la malaltia."),
    ("Què és la febre?", "La febre és l'augment de la temperatura corporal per sobre dels valors normals, generalment com a resposta del sistema immunitari a una infecció."),
    ("Què és la penicil·lina?", "La penicil·lina és un antibiòtic descobert per Alexander Fleming el 1928, que va revolucionar el tractament de les infeccions bacterianes."),
    ("Què és el sistema immunitari?", "El sistema immunitari és el conjunt de cèl·lules, teixits i òrgans que protegeixen el cos contra microorganismes i altres agents invasors."),
    ("Què és una cèl·lula?", "La cèl·lula és la unitat fonamental de la vida: tots els éssers vius estan formats per una o més cèl·lules, que realitzen les funcions vitals."),
    ("Què és l'ADN?", "L'ADN és la molècula que conté la informació genètica heretable, organitzada en gens, i que dirigeix la síntesi de proteïnes de l'organisme."),
    # ── física i química ──
    ("Què és la gravetat?", "La gravetat és la força d'atracció entre masses; és la que manté els objectes a la superfície de la Terra i regeix l'òrbita dels planetes."),
    ("Què és un àtom?", "L'àtom és la unitat més petita d'un element químic que conserva les seves propietats, format per protons, neutrons i electrons."),
    ("Què és la fotosíntesi?", "La fotosíntesi és el procés pel qual les plantes converteixen llum, aigua i diòxid de carboni en glucosa i oxigen."),
    ("Què és l'aigua?", "L'aigua és un compost químic format per dos àtoms d'hidrogen i un d'oxigen (H2O), essencial per a la vida i el dissolvent universal."),
    ("Què és la velocitat de la llum?", "La velocitat de la llum al buit és d'aproximadament 299.792 quilòmetres per segon, i és la velocitat màxima de l'univers."),
    ("Què és la pressió atmosfèrica?", "La pressió atmosfèrica és la força que exerceix l'aire sobre la superfície terrestre, causada pel pes de la columna d'aire."),
    ("Què és un imant?", "Un imant és un objecte que genera un camp magnètic i atrau materials ferromagnètics com el ferro, el níquel o el cobalt."),
    ("Què és l'energia cinètica?", "L'energia cinètica és l'energia que té un cos pel fet de moure's; depèn de la seva massa i del quadrat de la seva velocitat."),
    # ── història ──
    ("Qui va ser Maria Montessori?", "Maria Montessori va ser una pedagoga italiana (1870-1952), creadora del mètode educatiu que porta el seu nom, basat en l'autonomia de l'infant."),
    ("Què va ser el Renaixement?", "El Renaixement va ser un moviment cultural i artístic dels segles XV i XVI, originat a Itàlia, que va recuperar els valors clàssics grecollatins."),
    ("Què va ser la Revolució Industrial?", "La Revolució Industrial va ser el procés de canvi econòmic i social iniciat a Gran Bretanya al segle XVIII, basat en la mecanització de la producció."),
    ("Qui va ser Hipàtia d'Alexandria?", "Hipàtia d'Alexandria va ser una filòsofa, matemàtica i astrònoma del segle IV, considerada una de les primeres dones científiques de la història."),
    ("Què va ser l'Imperi Romà?", "L'Imperi Romà va ser una civilització que va dominar la conca mediterrània durant segles, amb un dret, una llengua i unes infraestructures que van marcar Europa."),
    ("Què va ser la Il·lustració?", "La Il·lustració va ser un moviment intel·lectual del segle XVIII que va defensar la raó, la ciència i els drets individuals davant l'absolutisme."),
    ("Qui va ser Ramon Llull?", "Ramon Llull va ser un filòsof, escriptor i místic mallorquí del segle XIII, pioner de la llengua catalana literària i de la lògica computacional."),
    ("Què va ser la Guerra de Successió?", "La Guerra de Successió (1701-1714) va ser un conflicte europeu sobre el tron espanyol; la seva derrota va comportar la pèrdua de les institucions pròpies de Catalunya."),
    # ── geografia ──
    ("Quin és el riu més llarg del món?", "El riu més llarg del món és el Nil, amb uns 6.650 quilòmetres, tot i que alguns estudiosos consideren l'Amazones més llarg si es compten els seus afluents."),
    ("Què és el desert del Sàhara?", "El Sàhara és el desert càlid més gran del món, amb més de nou milions de quilòmetres quadrats, situat al nord d'Àfrica."),
    ("Quina és la capital de Noruega?", "La capital de Noruega és Oslo, situada al sud-est del país, a la riba del fiord d'Oslo."),
    ("Què és l'illa de Menorca?", "Menorca és una illa de les Balears, declarada Reserva de la Biosfera el 1993, coneguda per les seves cales i la seva biodiversitat."),
    ("Quin oceà envolta l'Antàrtida?", "L'Antàrtida és envoltada per l'oceà Antàrtic, també anomenat oceà Glacial Antàrtic, el quart oceà més gran del món."),
    ("Quina és la muntanya més alta de la Terra?", "La muntanya més alta de la Terra és l'Everest, amb 8.848 metres sobre el nivell del mar, situada a la serralada de l'Himàlaia."),
    ("Què és la mar Mediterrània?", "La Mediterrània és una mar interior entre Europa, Àfrica i Àsia, bressol de civilitzacions com la grega i la romana."),
    ("Quin és el país més gran del món?", "El país més gran del món és Rússia, amb més de disset milions de quilòmetres quadrats, que s'estén per Europa i Àsia."),
    # ── llengua i literatura ──
    ("Què és un substantiu?", "Un substantiu és una categoria gramatical que designa persones, animals, coses, llocs o idees; en català té gènere i nombre."),
    ("Què és una metàfora?", "Una metàfora és una figura retòrica que identifica un terme real amb un d'imaginarí per la seva semblança, com en la frase els seus ulls són estrelles."),
    ("Qui va escriure Tirant lo Blanc?", "Tirant lo Blanc va ser escrit per Joanot Martorell i publicat el 1490; és considerada una de les obres més importants de la novel·la cavalleresca europea."),
    ("Què és el verb ser?", "El verb ser és un verb copulatiu que atribueix una qualitat o identitat al subjecte, com en la frase la Terra és un planeta."),
    ("Què és una sinònim?", "Un sinònim és una paraula que té un significat igual o molt semblant al d'una altra, com ara alegre i content."),
    ("Què és la sintaxi?", "La sintaxi és la part de la gramàtica que estudia com s'ordenen i es combinen les paraules per formar oracions amb sentit."),
    ("Qui va ser Mercè Rodoreda?", "Mercè Rodoreda va ser una escriptora catalana (1908-1983), autora de La plaça del Diamant, una de les novel·les cabdals de la literatura catalana."),
    ("Què és un refrany?", "Un refrany és una dita popular breu i sentenciosa que transmet saviesa tradicional, com ara qui dia passa, any empeny."),
    # ── matemàtiques ──
    ("Què és un nombre primer?", "Un nombre primer és un nombre natural més gran que 1 que només té dos divisors: ell mateix i la unitat, com el 2, el 3 o el 5."),
    ("Què és el teorema de Pitàgores?", "El teorema de Pitàgores afirma que en un triangle rectangle, el quadrat de la hipotenusa és igual a la suma dels quadrats dels catets."),
    ("Què és el nombre pi?", "El nombre pi és la constant que relaciona la longitud d'una circumferència amb el seu diàmetre, aproximadament 3,14159."),
    ("Què és una equació?", "Una equació és una igualtat matemàtica amb una o més incògnites que es compleix per a determinats valors, com ara 2x més 3 igual a 7."),
    ("Què és la mitjana aritmètica?", "La mitjana aritmètica és el resultat de sumar un conjunt de valors i dividir-lo pel nombre de valors; és una mesura de tendència central."),
    ("Què és un quadrilàter?", "Un quadrilàter és un polígon de quatre costats; exemples en són el quadrat, el rectangle, el rombe i el trapezi."),
    ("Què és la probabilitat?", "La probabilitat és la mesura numèrica de la possibilitat que esdevingui un esdeveniment, expressada entre 0 i 1."),
    ("Què és una fracció?", "Una fracció és una expressió que representa una part d'un tot, formada per un numerador i un denominador, com ara tres quarts."),
    # ── astronomia ──
    ("Què és un planeta?", "Un planeta és un cos celeste que orbita al voltant d'una estrella, té prou massa per ser esfèric i ha netejat la seva òrbita."),
    ("Què és la Lluna?", "La Lluna és el satèl·lit natural de la Terra; influeix en les marees i triga uns 27,3 dies a completar una òrbita."),
    ("Què és una estrella?", "Una estrella és una esfera de plasma que genera energia per fusió nuclear; el Sol n'és l'exemple més proper."),
    ("Què és un forat negre?", "Un forat negre és una regió de l'espai amb una gravetat tan intensa que res, ni tan sols la llum, pot escapar-ne."),
    ("Què és la Via Làctia?", "La Via Làctia és la galàxia espiral on es troba el sistema solar, amb centenars de milers de milions d'estrelles."),
    ("Què és un eclipsi?", "Un eclipsi és l'ocultació d'un astre per un altre; els més coneguts són els de Sol i els de Lluna."),
    ("Què és el sistema solar?", "El sistema solar és el conjunt format pel Sol i els cossos que hi orbiten: vuit planetes, planetes nans, asteroides i cometes."),
    ("Què és un cometa?", "Un cometa és un cos celeste format per gel, roca i pols que, en acostar-se al Sol, desenvolupa una cabellera i una cua visibles."),
    # ── tecnologia i informàtica ──
    ("Què és un algorisme?", "Un algorisme és una seqüència finita i ordenada d'instruccions que resol un problema o executa una tasca de manera determinista."),
    ("Què és la intel·ligència artificial?", "La intel·ligència artificial és la disciplina que construeix sistemes capaços de realitzar tasques que normalment requereixen intel·ligència humana, com comprendre llenguatge o reconèixer imatges."),
    ("Què és una xarxa neuronal?", "Una xarxa neuronal és un model computacional inspirat en el cervell, format per capes de nodes interconnectats, que aprèn patrons a partir de dades."),
    ("Què és un llenguatge de programació?", "Un llenguatge de programació és un sistema formal amb el qual s'escriuen instruccions que un ordinador pot executar; exemples en són Python i C."),
    ("Què és el programari lliure?", "El programari lliure és aquell que respecta la llibertat dels usuaris d'executar, estudiar, modificar i redistribuir el programa."),
    ("Què és un servidor?", "Un servidor és un ordinador o programa que ofereix serveis a altres equips, anomenats clients, a través d'una xarxa."),
    ("Què és la ciberseguretat?", "La ciberseguretat és el conjunt de pràctiques i tecnologies destinades a protegir sistemes, xarxes i dades d'atacs i accés no autoritzat."),
    ("Què és una base de dades?", "Una base de dades és un conjunt organitzat de dades emmagatzemat de manera estructurada i consultable, normalment mitjançant SQL."),
    # ── cultura i cuina ──
    ("Què és la paella?", "La paella és un plat tradicional valencià fet amb arròs, safrà i diversos ingredients com verdures, conill o marisc, cuit en una paella ampla."),
    ("Què és la sardana?", "La sardana és una dansa tradicional catalana que es balla en cercle, acompanyada de la música de la cobla."),
    ("Què és el castell?", "Un castell és una torre humana formada per persones que s'enfilen les unes sobre les altres, una tradició festiva molt arrelada a Catalunya."),
    ("Què és la Festa Major?", "La Festa Major és la celebració anual d'un poble o ciutat, amb actes populars com cercaviles, concerts, balls i activitats per a tothom."),
    ("Què és el Pa amb tomàquet?", "El pa amb tomàquet és una preparació culinària catalana feta amb pa, tomàquet madur, oli d'oliva i sal, sovint acompanyada d'embotits."),
    ("Què és la música clàssica?", "La música clàssica és la tradició musical culta occidental, composta generalment per a orquestra o conjunts de cambra, amb autors com Mozart i Beethoven."),
    ("Què és el futbol?", "El futbol és un esport d'equip en què dos equips d'onze jugadors intenten introduir la pilota a la porteria contrària, sense fer-la servir amb les mans."),
    ("Què és una biblioteca?", "Una biblioteca és un espai que conserva, organitza i ofereix llibres i altres documents per a la lectura, l'estudi i la consulta pública."),
]

VALIDATION = [
    ("Què és la pressió arterial?", "La pressió arterial és la força que la sang exerceix sobre les parets de les artèries; té dos valors, la sistòlica i la diastòlica."),
    ("Què és el plàstic?", "El plàstic és un material sintètic derivat majoritàriament del petroli, lleuger i modelable, però molt poc biodegradable."),
    ("Qui va ser Isaac Newton?", "Isaac Newton va ser un científic anglès (1643-1727) que va formular les lleis del moviment i la llei de la gravitació universal."),
    ("Què és el Pirineu?", "El Pirineu és la serralada muntanyosa que separa la península Ibèrica de la resta d'Europa, amb el pic d'Aneto com a cota més alta."),
    ("Què és una oració subordinada?", "Una oració subordinada és la que depèn sintàcticament d'una oració principal, com en l'exemple espero que vinguis aviat."),
    ("Què és el logaritme?", "El logaritme és l'operació inversa de la potenciació: el logaritme en base 10 de 100 és 2, perquè 10 elevat a 2 és 100."),
    ("Què és la teoria de la relativitat?", "La teoria de la relativitat, formulada per Albert Einstein, descriu com l'espai i el temps es corben per la matèria i l'energia."),
    ("Què és el correu electrònic?", "El correu electrònic és un servei de missatgeria que permet enviar i rebre missatges i fitxers a través d'Internet, mitjançant adreces com usuari@domini."),
    ("Què és el cava?", "El cava és un vi escumós elaborat amb el mètode tradicional, amb denominació d'origen a Catalunya, especialment al Penedès."),
    ("Què és una enciclopèdia?", "Una enciclopèdia és una obra de referència que recull i organitza el coneixement humà per ordre alfabètic o temàtic."),
]

TEST = [
    ("Què és la hipertensió arterial?", "La hipertensió arterial és l'elevació sostinguda de la pressió de la sang sobre les parets de les artèries, per sobre dels valors considerats normals."),
    ("Què és un ecosistema?", "Un ecosistema és el conjunt format per una comunitat d'organismes i el seu medi físic, amb les interaccions que s'hi estableixen."),
    ("Qui va ser Leonardo da Vinci?", "Leonardo da Vinci va ser un artista i científic italià del Renaixement, autor de La Gioconda i de nombrosos estudis d'anatomia i enginyeria."),
    ("Quin és el continent més poblat?", "El continent més poblat és Àsia, amb més de quatre mil milions d'habitants, aproximadament el 60 per cent de la població mundial."),
    ("Què és una antònim?", "Un antònim és una paraula que té un significat oposat al d'una altra, com ara fred i calor."),
    ("Què és la derivada?", "La derivada és una operació del càlcul que mesura la taxa de variació instantània d'una funció en un punt."),
    ("Què és la biosfera?", "La biosfera és la capa de la Terra on es desenvolupa la vida, que comprèn parts de la litosfera, la hidrosfera i l'atmosfera."),
    ("Què és un sistema operatiu?", "Un sistema operatiu és el programari que gestiona els recursos d'un ordinador i ofereix una interfície entre l'usuari i el maquinari; exemples en són Linux i Windows."),
    ("Què és la UNESCO?", "La UNESCO és l'organisme de les Nacions Unides per a l'educació, la ciència i la cultura, amb seu a París."),
    ("Què és l'arquitectura gòtica?", "L'arquitectura gòtica és un estil medieval caracteritzat per les arcades apuntades, els vitralls i les voltes de creueria, present a catedrals com la de Barcelona."),
]


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "qwen3_pilot_data"
    os.makedirs(data_dir, exist_ok=True)
    # dos exemples amb format messages (format opcional) al train
    train = [{"instruction": i, "response": r} for i, r in TRAIN]
    train = train[:58] + [
        {"messages": [{"role": "user", "content": "Què és el clima?"},
                      {"role": "assistant", "content": "El clima és el conjunt de condicions atmosfèriques mitjanes d'una regió al llarg de molts anys, com la temperatura i les precipitacions."}]},
        {"messages": [{"role": "user", "content": "Què és un poema?"},
                      {"role": "assistant", "content": "Un poema és una composició literària en vers que expressa sentiments, idees o experiències amb un llenguatge sovint figurat i rítmic."}]},
    ]
    val = [{"instruction": i, "response": r} for i, r in VALIDATION]
    test = [{"instruction": i, "response": r} for i, r in TEST]
    for name, rows in (("train", train), ("validation", val), ("test", test)):
        with open(os.path.join(data_dir, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for ex in rows:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")
        print(f"{name}: {len(rows)} exemples")


if __name__ == "__main__":
    main()
