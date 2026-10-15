"""GUI translations for the ST7796 panel.

The Portuguese text is the key: a missing entry simply falls back to the
source string, so the panel always shows something sensible.

Used by st7796.py via tr(); the active language is set from the config
(`language`) and can be changed at runtime from Mais... -> Idioma (the
choice is stored in ~/printer_data/config/gui_language.txt).
"""

LANGS = ('pt', 'en', 'es', 'fr')
NAMES = {'pt': 'Português', 'en': 'English', 'es': 'Español',
         'fr': 'Français'}
# Short tag shown on the menu tile (max 12 chars with the label).
TAGS = {'pt': 'PT', 'en': 'EN', 'es': 'ES', 'fr': 'FR'}

LANG = 'pt'

EN = {
    # --- menus ---
    'Mover': 'Move', 'Temperatura': 'Temperature', 'Extruder': 'Extruder',
    'Imprimir': 'Print', 'Nivelar': 'Level', 'Z Calibrar': 'Z Calibrate',
    'Mais': 'More', 'Mais...': 'More...', 'Voltar': 'Back',
    'Home All': 'Home All', 'Home XY': 'Home XY',
    'Arrefecer': 'Cool', 'Carregar': 'Load', 'Descarga': 'Unload',
    'Bico 200': 'Nozzle 200', 'Bico 240': 'Nozzle 240', 'Cama 60': 'Bed 60',
    'Guardar': 'Save', 'Rede': 'Network', 'Beep': 'Beep',
    'Motores OFF': 'Motors OFF', 'Avançadas': 'Advanced', 'Limites': 'Limits',
    'Emergency': 'Emergency', 'Reboot': 'Reboot', 'Desligar': 'Shutdown',
    'Filamento': 'Filament', 'Idioma': 'Language',
    'Mais 2/2': 'More 2/2', 'Extruder 2/2': 'Extruder 2/2',
    'Nivelar cama': 'Level bed',
    # --- itens (tiles/listas) ---
    'Bico': 'Nozzle', 'Cama': 'Bed', 'Fan': 'Fan', 'Flow': 'Flow',
    'Speed': 'Speed', 'Baby': 'Baby', 'Pressure Advance': 'Pressure Advance',
    'Smooth Time': 'Smooth Time', 'Shaper X': 'Shaper X',
    'Shaper Y': 'Shaper Y', 'Velocity': 'Velocity',
    'Acceleration': 'Accel', 'Square Corner': 'Sq Corner',
    'Min Cruise Ratio': 'Min Cruise',
    # --- imprimir ---
    'sem preview': 'no preview', 'CAMADA %s/%s': 'LAYER %s/%s',
    'FALTA %d:%02d': 'LEFT %d:%02d', 'TEMPO %d:%02d': 'TIME %d:%02d',
    'Pausa': 'Pause', 'Continuar': 'Resume', 'Cancelar': 'Cancel',
    'Beep ON': 'Beep ON', 'Beep OFF': 'Beep OFF',
    'Filam ON': 'Filament ON', 'Filam OFF': 'Filament OFF',
    '(live - guardado %+.3f)': '(live - saved %+.3f)',
    'IMPRIMIR AGORA?': 'PRINT NOW?', 'Imprimir ficheiro': 'Print file',
    # --- nivelamento ---
    'VERIFICAR SE O BICO': 'CHECK THE NOZZLE',
    'ESTA SUJO (LIMPAR ANTES)': 'IS DIRTY (CLEAN FIRST)',
    'AJUSTAR': 'ADJUST', 'INICIAR': 'START', 'ABORTAR': 'ABORT',
    'CONFIRMAR': 'CONFIRM', 'CONFIRMAR?': 'CONFIRM?',
    'Ponto %d / %d': 'Point %d / %d',
    'NIVELAMENTO DA CAMA': 'BED LEVELING',
    'Vai aquecer a cama a 60C e o bico a 200C':
        'It will heat the bed to 60C and the nozzle to 200C',
    'para fazer o teste de nivelamento.':
        'to run the leveling test.',
    'LIMPA O BICO ANTES DE CONTINUAR!': 'CLEAN THE NOZZLE BEFORE CONTINUING!',
    'Aquece cama+bico - limpa o bico!': 'Heats bed+nozzle - clean it!',
    # --- ajustar ---
    'AJUSTE': 'ADJUST', 'PONTO %d,%d': 'POINT %d,%d', 'PASSO': 'STEP',
    'GRAVAR': 'SAVE', 'GRAVAR no printer.cfg': 'SAVE to printer.cfg',
    'Nada para gravar': 'Nothing to save',
    'Nada para gravar neste ecra': 'Nothing to save here',
    'Limite do ajuste (+/-1.0mm)': 'Adjustment limit (+/-1.0mm)',
    'Sem malha da cama - faz Nivelar primeiro':
        'No bed mesh - run Level first',
    'Toca primeiro num ponto da grelha': 'Tap a grid point first',
    'Toca outra vez para ABORTAR (M112)': 'Tap again to ABORT (M112)',
    'Nivelamento abortado (M112)': 'Leveling aborted (M112)',
    # --- avisos ---
    'Impressao em curso - M84 bloqueado': 'Printing - M84 blocked',
    'Impressao em curso - cancelar primeiro': 'Printing - cancel first',
    'AVISO: bico %.0fC (min %.0fC) - extrusao bloqueada':
        'WARNING: nozzle %.0fC (min %.0fC) - extrusion blocked',
    'Falha ao anunciar na rede': 'Network announce failed',
    'EMERGENCY STOP !': 'EMERGENCY STOP !',
    'A iniciar...': 'Starting...',
    'A gravar o baby step (SAVE_CONFIG)...': 'Saving the baby step...',
    'A introduzir o filamento (~100mm)...': 'Loading filament (~100mm)...',
    'A retirar o filamento...': 'Unloading filament...',
    'A reiniciar o host...': 'Rebooting the host...',
    'A desligar o host...': 'Shutting the host down...',
}

ES = {
    'Mover': 'Mover', 'Temperatura': 'Temperatura', 'Extruder': 'Extrusor',
    'Imprimir': 'Imprimir', 'Nivelar': 'Nivelar', 'Z Calibrar': 'Calibrar Z',
    'Mais': 'Más', 'Mais...': 'Más...', 'Voltar': 'Volver',
    'Arrefecer': 'Enfriar', 'Carregar': 'Cargar', 'Descarga': 'Descargar',
    'Bico 200': 'Boquilla 200', 'Bico 240': 'Boquilla 240',
    'Cama 60': 'Cama 60', 'Guardar': 'Guardar', 'Rede': 'Red',
    'Motores OFF': 'Motores OFF', 'Avançadas': 'Avanzado',
    'Limites': 'Límites', 'Reboot': 'Reiniciar', 'Desligar': 'Apagar',
    'Filamento': 'Filamento', 'Idioma': 'Idioma',
    'Mais 2/2': 'Más 2/2', 'Extruder 2/2': 'Extrusor 2/2',
    'Bico': 'Boquilla', 'Cama': 'Cama', 'Fan': 'Ventilador',
    'Flow': 'Flujo', 'Speed': 'Velocidad',
    'Acceleration': 'Aceleración', 'Square Corner': 'Esq. curva',
    'Min Cruise Ratio': 'Crucero mín',
    'sem preview': 'sin preview', 'CAMADA %s/%s': 'CAPA %s/%s',
    'FALTA %d:%02d': 'RESTA %d:%02d', 'TEMPO %d:%02d': 'TIEMPO %d:%02d',
    'Pausa': 'Pausa', 'Continuar': 'Continuar', 'Cancelar': 'Cancelar',
    'Beep ON': 'Bip ON', 'Beep OFF': 'Bip OFF',
    'Filam ON': 'Filamento ON', 'Filam OFF': 'Filamento OFF',
    'VERIFICAR SE O BICO': 'COMPROBAR LA BOQUILLA',
    'ESTA SUJO (LIMPAR ANTES)': 'ESTA SUCIO (LIMPIAR ANTES)',
    'AJUSTAR': 'AJUSTAR', 'INICIAR': 'INICIAR', 'ABORTAR': 'ABORTAR',
    'CONFIRMAR': 'CONFIRMAR', 'CONFIRMAR?': '¿CONFIRMAR?',
    'Ponto %d / %d': 'Punto %d / %d',
    'NIVELAMENTO DA CAMA': 'NIVELADO DE CAMA',
    'LIMPA O BICO ANTES DE CONTINUAR!': '¡LIMPIA LA BOQUILLA ANTES!',
    'AJUSTE': 'AJUSTE', 'PONTO %d,%d': 'PUNTO %d,%d', 'PASSO': 'PASO',
    'GRAVAR': 'GRABAR', 'GRAVAR no printer.cfg': 'GRABAR en printer.cfg',
    'Nada para gravar': 'Nada que grabar',
    'Nada para gravar neste ecra': 'Nada que grabar aquí',
    'Sem malha da cama - faz Nivelar primeiro':
        'Sin malla de cama - haz Nivelar primero',
    'Toca primeiro num ponto da grelha': 'Toca primero un punto',
    'Impressao em curso - M84 bloqueado': 'Imprimiendo - M84 bloqueado',
    'Impressao em curso - cancelar primeiro': 'Imprimiendo - cancela primero',
    'Falha ao anunciar na rede': 'Fallo al anunciar en la red',
    'A reiniciar o host...': 'Reiniciando el host...',
    'A desligar o host...': 'Apagando el host...',
}

FR = {
    'Mover': 'Déplacer', 'Temperatura': 'Température',
    'Extruder': 'Extrudeur', 'Imprimir': 'Imprimer', 'Nivelar': 'Niveler',
    'Z Calibrar': 'Calibrer Z', 'Mais': 'Plus', 'Mais...': 'Plus...',
    'Voltar': 'Retour', 'Arrefecer': 'Refroidir', 'Carregar': 'Charger',
    'Descarga': 'Décharger', 'Bico 200': 'Buse 200', 'Bico 240': 'Buse 240',
    'Cama 60': 'Lit 60', 'Guardar': 'Sauver', 'Rede': 'Réseau',
    'Motores OFF': 'Moteurs OFF', 'Avançadas': 'Avancé',
    'Limites': 'Limites', 'Reboot': 'Redémarrer', 'Desligar': 'Éteindre',
    'Filamento': 'Filament', 'Idioma': 'Langue',
    'Mais 2/2': 'Plus 2/2', 'Extruder 2/2': 'Extrudeur 2/2',
    'Bico': 'Buse', 'Cama': 'Lit', 'Fan': 'Ventilateur', 'Flow': 'Débit',
    'Speed': 'Vitesse', 'Acceleration': 'Accélération',
    'Square Corner': 'Coin carré', 'Min Cruise Ratio': 'Croisière min',
    'sem preview': 'sans aperçu', 'CAMADA %s/%s': 'COUCHE %s/%s',
    'FALTA %d:%02d': 'RESTE %d:%02d', 'TEMPO %d:%02d': 'TEMPS %d:%02d',
    'Pausa': 'Pause', 'Continuar': 'Reprendre', 'Cancelar': 'Annuler',
    'Beep ON': 'Bip ON', 'Beep OFF': 'Bip OFF',
    'Filam ON': 'Filament ON', 'Filam OFF': 'Filament OFF',
    'VERIFICAR SE O BICO': 'VÉRIFIER LA BUSE',
    'ESTA SUJO (LIMPAR ANTES)': 'EST SALE (NETTOYER)',
    'AJUSTAR': 'RÉGLER', 'INICIAR': 'DÉMARRER', 'ABORTAR': 'ABANDONNER',
    'CONFIRMAR': 'CONFIRMER', 'CONFIRMAR?': 'CONFIRMER ?',
    'Ponto %d / %d': 'Point %d / %d',
    'NIVELAMENTO DA CAMA': 'NIVELLEMENT DU LIT',
    'LIMPA O BICO ANTES DE CONTINUAR!': 'NETTOIE LA BUSE AVANT !',
    'AJUSTE': 'RÉGLAGE', 'PONTO %d,%d': 'POINT %d,%d', 'PASSO': 'PAS',
    'GRAVAR': 'SAUVER', 'GRAVAR no printer.cfg': 'SAUVER dans printer.cfg',
    'Nada para gravar': 'Rien à sauver',
    'Nada para gravar neste ecra': 'Rien à sauver ici',
    'Sem malha da cama - faz Nivelar primeiro':
        'Pas de maillage - lance Niveler',
    'Toca primeiro num ponto da grelha': "Touche d'abord un point",
    'Impressao em curso - M84 bloqueado': 'Impression - M84 bloqué',
    'Impressao em curso - cancelar primeiro': 'Impression - annule avant',
    'Falha ao anunciar na rede': 'Échec de l\'annonce réseau',
    'A reiniciar o host...': 'Redémarrage du host...',
    'A desligar o host...': 'Extinction du host...',
}

T = {'en': EN, 'es': ES, 'fr': FR}


def set_lang(lang):
    global LANG
    if lang in LANGS:
        LANG = lang
    return LANG


def next_lang():
    try:
        i = LANGS.index(LANG)
    except ValueError:
        i = 0
    return set_lang(LANGS[(i + 1) % len(LANGS)])


def tr(s):
    if LANG == 'pt' or not s:
        return s
    return T.get(LANG, {}).get(s, s)
