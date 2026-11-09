import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Store, compatible, price_to_cents, read_inventory_csv


class MonitorTests(unittest.TestCase):
    def test_price_formats(self):
        self.assertEqual(price_to_cents('R$ 1.234,50'), 123450)
        self.assertEqual(price_to_cents('1,00'), 100)
        self.assertEqual(price_to_cents('0.50'), 50)
        self.assertIsNone(price_to_cents(''))

    def test_import_portuguese_semicolon(self):
        data = ('Nome;Edição;Preço;Quantidade;Idioma\n'
                'The First Sliver;MH1;R$ 48,90;2;Inglês\n'
                'Sol Ring;CMD;2,50;0;Português\n')
        rows = read_inventory_csv(data)
        self.assertEqual(rows[0]['price_cents'], 4890)
        self.assertEqual(rows[0]['quantity'], 2)
        self.assertEqual(rows[1]['name'], 'Sol Ring')
        self.assertEqual(rows[1]['quantity'], 0)

    def test_unknown_quantity_remains_unknown(self):
        data = 'name,price\nArcane Signet,2.50\n'
        row = read_inventory_csv(data)[0]
        self.assertIsNone(row['quantity'])
        self.assertEqual(row['price_cents'], 250)

    def test_empty_and_wrong_headers(self):
        with self.assertRaises(ValueError):
            read_inventory_csv('produto,cor\nSome card,blue\n')
        with self.assertRaises(ValueError):
            read_inventory_csv('name,quantity\n,2\n')

    def test_snapshots_diff_no_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            db = Store(Path(d) / 'test.sqlite3')
            prev = read_inventory_csv('name;price;quantity\nSwords to Plowshares;1,00;4\nSol Ring;2,00;1\n')
            now = read_inventory_csv('name;price;quantity\nSwords to Plowshares;1,50;3\nArcane Signet;1,00;4\n')
            db.import_rows(prev, 'csv_upload', 'first.csv')
            db.import_rows(now, 'csv_upload', 'second.csv')
            self.assertEqual(db.status()['stats']['names'], 2)
            self.assertEqual(len(db.snapshots()), 2)
            dff = db.changes()
            self.assertEqual(len(dff), 3)
            cats = {r['name']: r['category'] for r in dff}
            self.assertEqual(cats['Sol Ring'], 'saida')
            self.assertEqual(cats['Arcane Signet'], 'entrada')
            self.assertEqual(cats['Swords to Plowshares'], 'alteracao')
            self.assertEqual(len(db.cards(query='arcane')), 1)
            db.conn.close()

    def test_pagination_price_sort_and_literal_search(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / 'test.sqlite3')
            rows = read_inventory_csv('name;price;quantity;set_code\nAlpha;5,00;2;CMM\nBeta;2,00;4;M11\nGamma;9,00;1;CMM\n')
            store.import_rows(rows, 'csv_upload', 'page.csv')
            self.assertEqual(store.count_cards(only_stock=True), 3)
            self.assertEqual([r['name'] for r in store.cards(sort='price_asc', limit=2)], ['Beta', 'Alpha'])
            self.assertEqual(store.cards(sort='price_asc', limit=2, offset=2)[0]['name'], 'Gamma')
            self.assertEqual(store.count_cards(set_code='CMM'), 2)
            self.assertEqual(store.count_cards(query='%'), 0)
            store.conn.close()

    def test_backup_restore_all_snapshots_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'one.sqlite3')
            rows=read_inventory_csv('name;price;quantity\nSol Ring;2,50;2\n')
            store.import_rows(rows,'csv_upload','import-1.csv')
            store.import_rows(rows,'historical_reference','import-old.csv')
            store.save_meta([('sol ring','Sol Ring','abc','[]','Artifact','',1,'legal','[]','', '2026-11-09T00:00:00+00:00')])
            raw=store.export_backup()
            self.assertGreater(len(raw),0)
            restored=Store(Path(d)/'two.sqlite3')
            results=restored.restore_backup(raw)
            self.assertEqual(results['snapshots'],2)
            self.assertEqual(results['offers'],2)
            self.assertEqual(results['metadata'],1)
            self.assertEqual(len(restored.snapshots()),2)
            self.assertEqual(restored.cards()[0]['name'],'Sol Ring')
            restored.conn.close();store.conn.close()

    def test_invalid_backup_does_not_destroy_existing_data(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db.sqlite3')
            store.import_rows(read_inventory_csv('name;quantity\nSol Ring;3\n'), 'csv_upload', 'original.csv')
            with self.assertRaises(ValueError):
                store.restore_backup(b'not a zip')
            self.assertEqual(len(store.snapshots()),1)
            self.assertEqual(store.cards()[0]['quantity'],3)
            store.conn.close()

    def test_color_legality(self):
        good = {'color_identity': json.dumps(['B']), 'legal_commander': 'legal'}
        bad = {'color_identity': json.dumps(['B', 'R']), 'legal_commander': 'legal'}
        banned = {'color_identity': json.dumps(['B']), 'legal_commander': 'banned'}
        unknown = {'color_identity': None, 'legal_commander': None}
        self.assertIs(compatible(good, ['B', 'G']), True)
        self.assertIs(compatible(bad, ['B', 'G']), False)
        self.assertIs(compatible(banned, ['B', 'G']), False)
        self.assertIsNone(compatible(unknown, ['B', 'G']))

if __name__ == '__main__':
    unittest.main()
