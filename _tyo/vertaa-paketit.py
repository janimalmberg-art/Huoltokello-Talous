# -*- coding: utf-8 -*-
"""Vertaa kahta Huoltokello-Talous-pakettia: lisatyt/poistetut/muuttuneet tiedostot.

Kaytto:  python vertaa-paketit.py <vanha.zip> <uusi.zip>
"""
import sys
import zipfile
import hashlib
from pathlib import PurePosixPath


def lue(zippolku):
    """Palauttaa {relatiivipolku: sha256} -sanakirjan paketin tiedostoista."""
    d = {}
    with zipfile.ZipFile(zippolku) as z:
        for tieto in z.infolist():
            if tieto.is_dir():
                continue
            osat = list(PurePosixPath(tieto.filename).parts)
            if osat and osat[0] == 'Huoltokello-Talous':
                osat = osat[1:]
            if not osat:
                continue
            d['/'.join(osat)] = hashlib.sha256(z.read(tieto)).hexdigest()
    return d


def main():
    if len(sys.argv) != 3:
        sys.exit('Kaytto: python vertaa-paketit.py <vanha.zip> <uusi.zip>')
    a = lue(sys.argv[1])
    b = lue(sys.argv[2])
    uudet = sorted(set(b) - set(a))
    poistetut = sorted(set(a) - set(b))
    muuttuneet = sorted(n for n in set(a) & set(b) if a[n] != b[n])
    for avain, lista in (('UUDET', uudet), ('POISTETUT', poistetut), ('MUUTTUNEET', muuttuneet)):
        print('%s (%d):' % (avain, len(lista)))
        for n in lista:
            print('  ' + n)
        print()


if __name__ == '__main__':
    main()
