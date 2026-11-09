import json
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import builder_engine as b


class BuilderEngineTests(TestCase):
    def test_store_id_exact_and_cheapest_qualifying(self):
        data={'ok': True, 'data': {'offers': [
            {'store_id': 33333, 'store_name':'Taberna Velha','price_brl':0.10,'quantity':4},
            {'store_id': 41369,'store_name':'Taberna Geek','price_brl':'R$ 2,50','quantity':1},
            {'store':{'id':41369,'name':'Taberna Geek'},'price_brl':3.49,'quantity':5,
             'edition_code':'CMM','url':'https://www.ligamagic.com.br/oferta-teste'},
        ]}}
        result=b.parse_reef_result(data,'Waste Not',2)
        self.assertEqual(result['status'],'available')
        self.assertEqual(result['price_cents'],349)
        self.assertEqual(result['quantity'],5)
        self.assertEqual(result['offer']['edition'],'CMM')
        self.assertEqual(len(result['offers']),2)

    def test_no_offer_not_equal_physical_zero(self):
        result=b.parse_reef_result({'ok':True,'data':{'offers':[
            {'store_id':11,'price_brl':2.50,'quantity':8}]}},'Sol Ring')
        self.assertEqual(result['status'],'not_listed')
        self.assertIn('não prova',result['message'])

    def test_unknown_on_missing_quantity(self):
        result=b.parse_reef_result({'ok':True,'data':{'offers':[
            {'store_id':41369,'price_brl':9.50}]}},'Arcane Signet')
        self.assertEqual(result['status'],'unknown')

    def test_unknown_on_unknown_response_shape(self):
        r=b.parse_reef_result({'ok':True,'data':{'some_price_data':[5]}},'Arcane Signet')
        self.assertEqual(r['status'],'unknown')

    def test_name_fallback_and_nested_printings(self):
        r=b.parse_reef_result({'ok':True,'data':{'prints':[
            {'offers':[{'store':{'name':'Taberna Geek'},'price_brl':1.25,'stock':2}]}
        ]}},'Heart Sliver')
        self.assertEqual(r['status'],'available')
        self.assertEqual(r['price_cents'],125)

    def test_unknown_if_2000_offers_without_target(self):
        r=b.parse_reef_result({'ok':True,'data':{'offers':[
            {'store_id':1,'price_brl':1,'quantity':1} for _ in range(2000)]}},'Sol Ring')
        self.assertEqual(r['status'],'unknown')

    def test_rate_target_preserves_role_and_commander(self):
        target={'name':'Waste Not','oracle_text':'Whenever an opponent discards a creature card, create a 2/2 black Zombie creature token.','type_line':'Enchantment','cmc':2}
        cmd={'name':'Nath of the Gilt-Leaf','oracle_text':'At the beginning of your upkeep, target opponent discards a card. Whenever an opponent discards a card, create a green Elf Warrior creature token.','type_line':'Legendary Creature — Elf Warrior'}
        good={'name':'Liliana\'s Caress','oracle_text':'Whenever an opponent discards a card, that player loses 2 life.','type_line':'Enchantment','cmc':2}
        irrelevant={'name':'Naturalize','oracle_text':'Destroy target artifact or enchantment.','type_line':'Instant','cmc':2}
        self.assertGreater(b.score_card(target,cmd,good)[0],0)
        self.assertEqual(b.score_card(target,cmd,irrelevant)[0],-100)

    def test_proposal_respects_identity_and_commander_legality(self):
        original={'name':'Waste Not','oracle_text':'Whenever an opponent discards a card, draw a card.',
                  'type_line':'Enchantment','cmc':2,'legalities':{'commander':'legal'},'color_identity':['B']}
        commander={'name':'Nath of the Gilt-Leaf','oracle_text':'Target opponent discards a card.',
                  'type_line':'Legendary Creature — Elf Warrior', 'color_identity':['B','G']}
        cards={'data':[
            {'name':'Dread Return','oracle_text':'Return target creature from your graveyard to the battlefield.','type_line':'Sorcery','cmc':4,'color_identity':['B'],'legalities':{'commander':'legal'}},
            {'name':'Liliana\'s Caress','oracle_text':'Whenever an opponent discards a card, that player loses 2 life.','type_line':'Enchantment','cmc':2,'color_identity':['B'],'legalities':{'commander':'legal'}},
            {'name':'Wandering White Card','oracle_text':'Each opponent discards a card.','type_line':'Enchantment','cmc':2,'color_identity':['W'],'legalities':{'commander':'legal'}},
        ]}
        def fake_get(url,**kwargs):
            if '/cards/named?' in url and 'Waste%20Not' in url:return original
            if '/cards/named?' in url and 'Nath' in url:return commander
            if '/cards/search?' in url:return cards
            raise AssertionError(url)
        with patch.object(b,'fetch_json',side_effect=fake_get),patch.object(b.time,'sleep'):
            proposal=b.propose_replacements('Waste Not','Nath of the Gilt-Leaf')
        names=[c['name'] for c in proposal['candidates']]
        self.assertIn("Liliana's Caress",names)
        self.assertNotIn('Wandering White Card',names)
        self.assertNotIn('Dread Return',names)

if __name__=='__main__':
    import unittest
    unittest.main()
