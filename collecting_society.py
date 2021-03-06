# For copyright and license terms, see COPYRIGHT.rst (top level of repository)
# Repository: https://github.com/C3S/collecting_society
import os
import uuid
import datetime
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from collections import Counter, defaultdict
from sql.functions import CharLength
import hurry.filesize

from trytond.model import ModelView, ModelSQL, fields
from trytond.wizard import Wizard, StateView, Button, StateTransition

from trytond.transaction import Transaction
from trytond.pool import Pool
from trytond.pyson import Eval, Bool, Or, And


__all__ = [

    # Collecting Society
    'CollectingSociety',
    'TariffSystem',
    'TariffCategory',
    'Tariff',
    'Allocation',
    'Distribution',
    'DistributeStart',
    'Distribute',

    # Licenser
    'License',
    'Artist',
    'ArtistArtist',
    'ArtistRelease',
    'ArtistPayeeAcceptance',
    'ArtistIdentifier',
    'ArtistIdentifierName',
    'Creation',
    'CreationDerivative',
    'CreationContribution',
    'CreationContributionRole',
    'CreationRole',
    'CreationTariffCategory',
    'CreationIdentifier',
    'CreationIdentifierName',
    'Release',
    'ReleaseTrack',
    'ReleaseGenre',
    'ReleaseStyle',
    'ReleaseIdentifier',
    'ReleaseIdentifierName',
    'MixinIdentifier',
    'Genre',
    'Style',
    'Label',
    'Publisher',

    # Licensee
    'Utilisation',
    'Device',
    'Identifier',
    'Identification',
    'Fingerprintlog',

    # Archiving
    'Storehouse',
    'HarddiskLabel',
    'Harddisk',
    'HarddiskTest',
    'FilesystemLabel',
    'Filesystem',
    'Content',
    'Checksum',

    # Portal
    'AccessControlEntry',
    'AccessControlEntryRole',
    'AccessRole',
    'AccessRolePermission',
    'AccessPermission',

    # Tryton
    'STATES',
    'DEPENDS',

]
STATES = {
    'readonly': ~Eval('active'),
}
DEPENDS = ['active']
SEPARATOR = u' /25B6 '
DEFAULT_ACCESS_ROLES = ['Administrator', 'Stakeholder']


##############################################################################
# Mixins
##############################################################################


class MixinIdentifier(object):
    valid_from = fields.Date('Valid From Date')
    valid_to = fields.Date('Valid To Date')
    id_code = fields.Char('ID Code')


class CurrentState(object):
    'Mixin for the active state'
    active = fields.Boolean('Active')

    @staticmethod
    def default_active():
        return True


class ClaimState(object):
    'Mixin for the claim workflow'
    claim_state = fields.Selection(
        [
            ('unclaimed', 'Unclaimed'),
            ('claimed', 'Claimed'),
            ('revised', 'Revised'),
        ], 'Claim', states={'required': True}, sort=False,
        help='The state in a claim process.\n\n'
        '*Unclaimed*: Object is not yet claimed or a claim was cancelled.\n'
        '*Claimed*: Someone claimed this object but it was not revised yet.\n'
        '*Revised*: The claim was confirmed by administration')

    @staticmethod
    def default_claim_state():
        return "unclaimed"


class CommitState(object):
    'Mixin for the commit workflow'
    commit_state = fields.Selection(
        [
            ('uncommited', 'Uncommited'),
            ('commited', 'Commited'),
            ('revised', 'Revised'),
            ('rejected', 'Rejected'),
            ('deleted', 'Deleted'),
        ], 'Commit', states={'required': True}, sort=False,
        help='The state in a commit process.\n\n'
        '*Uncommited*: The object was freshly created.\n'
        '*Commited*: The object was commited by the web user.\n'
        '*Revised*: The commit was revised by administration.\n'
        '*Rejected*: The commit was rejected by administration.\n'
        '*Deleted*: The rejeted object was deleted.\n')

    @staticmethod
    def default_commit_state():
        return 'uncommited'


class EntityOrigin(object):
    'Mixin to track the origin of the entity'
    entity_origin = fields.Selection(
        [
            ('direct', 'Direct'),
            ('indirect', 'Indirect'),
        ], 'Entity Origin', states={'required': True}, sort=False,
        help='Defines, if an object was created as foreign object (indirect) '
             'or not.')
    entity_creator = fields.Many2One(
        'party.party', 'Entity Creator', states={'required': True})

    @staticmethod
    def default_entity_origin():
        return "direct"


class PublicApi(object):
    'Mixin to add an unique identifier for public use'
    oid = fields.Char(
        'OID', required=True,
        help='A unique object identifier used in the public web api to avoid'
             'exposure of implementation details to the users.')

    @classmethod
    def __setup__(cls):
        super(PublicApi, cls).__setup__()
        cls._sql_constraints += [
            ('uuid_oid', 'UNIQUE(oid)',
                'The OID of the object must be unique.'),
        ]

    @staticmethod
    def default_oid():
        return str(uuid.uuid4())


class AccessControlList(object):
    'Mixin to add an Access Control List'
    acl = fields.One2Many(
        'ace', 'entity', 'Access Control List',
        states=STATES, depends=DEPENDS,
        help='A list of acces control entries with object permissions.')

    def permits(self, web_user, code, derive=True):
        for ace in self.acl:
            if ace.web_user != web_user:
                continue
            for role in ace.roles:
                for permission in role.permissions:
                    if permission.code == code:
                        return True
        return False

    def permissions(self, web_user, valid_codes=False, derive=True):
        permissions = set()
        for ace in self.acl:
            if ace.web_user != web_user:
                continue
            permissions.update([
                permission.code
                for role in ace.roles
                for permission in role.permissions])
        if valid_codes:
            permissions = permissions.intersection(valid_codes)
        return tuple(permissions)


##############################################################################
# Collecting Society
##############################################################################


class CollectingSociety(ModelSQL, ModelView, PublicApi, CurrentState):
    'Collecting Society'
    __name__ = 'collecting_society'
    _history = True

    name = fields.Char(
        'Name', required=True, select=True, states=STATES, depends=DEPENDS)
    party = fields.Many2One(
        'party.party', 'Party', states=STATES, depends=DEPENDS,
        help='The legal person or organization acting the collecting society')
    represents_copyright = fields.Boolean(
        'Represents Copyright', help='The collecting society '
        'represents copyrights of authors')
    represents_ancillary_copyright = fields.Boolean(
        'Represents Ancillary Copyright', help='The collecting society '
        'represents ancillary copyights of performers')


class TariffSystem(ModelSQL, ModelView, CurrentState):
    'Tariff System'
    __name__ = 'tariff_system'
    _history = True
    _rec_name = 'version'

    code = fields.Char(
        'Code', required=True, select=True, states={'readonly': True})
    version = fields.Char(
        'Version', required=True, select=True, states=STATES, depends=DEPENDS)
    valid_from = fields.Date(
        'Valid from', help='Date from which the tariff is valid.')
    valid_through = fields.Date(
        'Valid through', help='Date thorugh which the tariff is valid.')
    transitional_through = fields.Date(
        'Transitional through',
        help='Date of the end of the transitinal phase, through which the '
        'tariff might still be used.')
    tariffs = fields.One2Many(
        'tariff_system.tariff', 'system', 'Tariffs',
        help='The tariffs of the tariff system.')
    # TODO: attachement
    # TODO: formluar
    # TODO: variables

    @classmethod
    def __setup__(cls):
        super(TariffSystem, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the tariff system must be unique.'),
            ('version_uniq', 'UNIQUE(version)',
             'The version of the tariff system must be unique.')
        ]

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(
                    config.tariff_system_sequence.id)
        return super(TariffSystem, cls).create(vlist)

    @classmethod
    def copy(cls, vlist, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(TariffSystem, cls).copy(vlist, default=default)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('version',) + tuple(clause[1:]),
        ]


class TariffCategory(ModelSQL, ModelView, CurrentState, PublicApi):
    'Tariff Category'
    __name__ = 'tariff_system.category'
    _history = True

    name = fields.Char(
        'Name', required=True, select=True, states=STATES, depends=DEPENDS)
    code = fields.Char(
        'Code', required=True, select=True, states=STATES, depends=DEPENDS)
    description = fields.Text(
        'Description', states=STATES, depends=DEPENDS,
        help='A description of the tariff category.')
    tariffs = fields.One2Many(
        'tariff_system.tariff', 'category', 'Tariffs',
        help='The tariffs in this tariff category.')
    # creations = fields.Many2Many(
    #     'creation-tariff_category', 'category', 'Creations',
    #     help='The creations in this tariff category.')

    @classmethod
    def __setup__(cls):
        super(TariffCategory, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the license must be unique.')
        ]

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @classmethod
    def copy(cls, vlist, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(TariffCategory, cls).copy(vlist, default=default)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('name',) + tuple(clause[1:]),
            ('code',) + tuple(clause[1:]),
        ]


class Tariff(ModelSQL, ModelView, CurrentState):
    'Tariff'
    __name__ = 'tariff_system.tariff'
    _history = True

    name = fields.Function(
        fields.Char('Name'), 'get_name', searcher='search_name')
    code = fields.Function(
        fields.Char('Code'), 'get_code', searcher='search_code')
    system = fields.Many2One(
        'tariff_system', 'System', required=True, select=True)
    category = fields.Many2One(
        'tariff_system.category', 'Category', required=True, select=True)
    # TODO: Variables

    def get_name(self, name):
        return self.category.name

    def get_code(self, name):
        return self.category.code + self.system.version

    def search_name(self, name):
        return self.get_title(name)

    def search_code(self, name):
        return self.get_code(name)


class Allocation(ModelSQL, ModelView):
    'Allocation'
    __name__ = 'distribution.allocation'
    distribution = fields.Many2One(
        'distribution', 'Distribution', required=True,
        help='The distribution of the allocation')
    type = fields.Selection(
        [
            ('pocket2hats', 'Pocket to Hats'),
            ('hat2pockets', 'Hat to Pockets'),
        ], 'Type', required=True, sort=False, help='The allocation type:\n'
        '*Pocket to Hats*: Allocates amount from a pocket to many hats\n'
        '*Hat to Pockets*: Allocates amount from a hat to many pockets')
    party = fields.Many2One(
        'party.party', 'Party', required=True,
        help='The party which utilises creations')
    currency_digits = fields.Function(
        fields.Integer('Currency Digits'), 'get_currency_digits')
    amount = fields.Numeric(
        'Amount', digits=(16, Eval('currency_digits', 2)),
        depends=['currency_digits'], help='The amount to distribute')
    share_amount = fields.Numeric(
        'Share Amount', digits=(16, Eval('currency_digits', 2)),
        depends=['currency_digits'], help='The share for each utilisation')
    move_lines = fields.One2Many(
        'account.move.line', 'origin', 'Account Move Lines',
        domain=[('origin', 'like', 'distribution.allocation,%')],
        help='The account move lines of the allocation')
    utilisations = fields.One2Many(
        'creation.utilisation', 'allocation', 'Utilisations',
        help='The allocated utilisations')

    @classmethod
    def __setup__(cls):
        super(Allocation, cls).__setup__()
        cls._order.insert(1, ('distribution', 'ASC'))
        cls._order.insert(2, ('party', 'ASC'))

    @staticmethod
    def default_type():
        return 'pocket2hats'

    def get_currency_digits(self, name):
        Company = Pool().get('company.company')
        if Transaction().context.get('company'):
            company = Company(Transaction().context['company'])
            return company.currency.digits
        return 2


class Distribution(ModelSQL, ModelView):
    'Distribution'
    __name__ = 'distribution'
    _rec_name = 'code'
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        })
    date = fields.Date(
        'Distribution Date', required=True, select=True,
        help='The date of the distribution')
    from_date = fields.Date(
        'From Date',
        help='Include utilisations equal or after from date')
    thru_date = fields.Date(
        'Thru Date', help='Include utilisations until thru date')
    allocations = fields.One2Many(
        'distribution.allocation', 'distribution', 'Allocations',
        help='All allocations in this distributon')

    @classmethod
    def __setup__(cls):
        super(Distribution, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the distribution must be unique.')
        ]
        cls._order.insert(1, ('date', 'ASC'))

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @staticmethod
    def default_date():
        Date = Pool().get('ir.date')
        return Date.today()

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(
                    config.distribution_sequence.id)
        return super(Distribution, cls).create(vlist)

    @classmethod
    def copy(cls, distributions, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(Distribution, cls).copy(distributions, default=default)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('date',) + tuple(clause[1:]),
        ]


class DistributeStart(ModelView):
    'Distribute Start'
    __name__ = 'distribution.distribute.start'

    date = fields.Date(
        'Distribution Date', required=True,
        help='The date of the distribution')
    from_date = fields.Date(
        'From Date', required=True,
        help='The earliest date to distribute utilisations')
    thru_date = fields.Date(
        'Thru Date', required=True,
        help='The latest date to distribute utilisations')

    @staticmethod
    def default_date():
        Date = Pool().get('ir.date')
        return Date.today()

    @staticmethod
    def default_from_date():
        Date = Pool().get('ir.date')
        t = Date.today()
        return datetime.date(t.year, t.month, 1) - relativedelta(months=1)

    @staticmethod
    def default_thru_date():
        Date = Pool().get('ir.date')
        return Date.today() - relativedelta(months=1) + relativedelta(day=31)


class Distribute(Wizard):
    "Distribute"
    __name__ = 'distribution.distribute'

    start = StateView(
        'distribution.distribute.start',
        'collecting_society.distribution_distribute_start_view_form',
        [
            Button('Cancel', 'end', 'tryton-cancel'),
            Button(
                'Start', 'distribute', 'tryton-ok', default=True),
        ])
    distribute = StateTransition()

    def transition_distribute(self):
        pool = Pool()
        Company = pool.get('company.company')
        Distribution = pool.get('distribution')
        Allocation = pool.get('distribution.allocation')
        Utilisation = pool.get('creation.utilisation')
        Account = pool.get('account.account')
        AccountMove = pool.get('account.move')
        AccountJournal = pool.get('account.journal')
        Party = pool.get('party.party')
        Period = pool.get('account.period')

        company = Company(Transaction().context['company'])
        currency = company.currency
        # TODO:
        # * Redistribution
        # * Check if distribution period overlaps with existing distribution

        # Collect utilisations
        utilisations = Utilisation.search([
            (
                'timestamp', '>=', datetime.datetime.combine(
                    self.start.from_date, datetime.time.min)
            ), (
                'timestamp', '<=', datetime.datetime.combine(
                    self.start.thru_date, datetime.time.max)
            ), ('state', '=', 'not_distributed'),
        ])
        if not utilisations:
            return 'end'
        # Create always a new distribution
        distribution, = Distribution.create(
            [
                {
                    'date': self.start.date,
                    'from_date': self.start.thru_date,
                    'thru_date': self.start.thru_date,
                }
            ]
        )

        party_utilisations = defaultdict(list)
        for utilisation in utilisations:
            party_utilisations[utilisation.party.id].append(utilisation)

        account_moves = []
        for party_id, utilisations in party_utilisations.iteritems():
            if not utilisations:
                continue
            party = Party(party_id)
            amount = party.pocket_balance
            if not amount:
                continue

            if party.pocket_budget < party.pocket_balance:
                amount = party.pocket_budget
            amount = currency.round(amount)
            fee_amount = currency.round(amount * Decimal(10) / Decimal(100))
            share_amount = currency.round(
                (amount - fee_amount) / len(utilisations))
            allocation = {
                'party': party_id,
                'distribution': distribution.id,
                'type': 'pocket2hats',
                'amount': amount,
                'share_amount': share_amount,
            }
            # create allocation
            allocation, = Allocation.create([allocation])

            Utilisation.write(
                utilisations,
                {
                    'state': 'processing',
                    'allocation': allocation.id,
                })
            account_move_lines = [{
                # Company fees move line
                'party': company.party.id,
                'artist': None,
                'account': Account.search([('kind', '=', 'revenue')])[0],
                'debit': Decimal(0),
                'credit': fee_amount,
                'state': 'draft',
            }, {
                # Pocket move line
                'party': party.id,
                'artist': None,
                'account': party.pocket_account.id,
                'debit': amount,
                'credit': Decimal(0),
                'state': 'draft',
            }]
            for utilisation in utilisations:
                breakdown = self._allocate(
                    utilisation.creation,
                    share_amount)
                for artist, amount in breakdown.iteritems():
                    account_move_lines += [{
                        # Hat move lines
                        'party': None,
                        'artist': artist.id,
                        'account': artist.hat_account.id,
                        'debit': Decimal(0),
                        'credit': currency.round(amount),
                        'state': 'draft',
                    }]
            period_id = Period.find(company.id, date=self.start.date)
            journal, = AccountJournal.search([('code', '=', 'TRANS')])
            origin = 'distribution.allocation,%s' % (allocation.id)
            account_moves.append({
                'journal': journal.id,
                'origin': origin,
                'date': self.start.date,
                'period': period_id,
                'state': 'draft',
                'lines': [('create', account_move_lines)],
            })
        AccountMove.create(account_moves)
        Utilisation.write(utilisations, {'state': 'distributed'})
        return 'end'

    def _allocate(self, creation, amount, result=None):
        '''
        Allocates an amount to all involved artists of a creation.
        The tree of original creations is traversed and every node creation is
        allocated by the appropriate derivative types.

        Returns a dictionary with artist as key and the sum of amounts
        as value.
        '''
        amount = Decimal(amount)

        if result is None:
            result = Counter()

        if not creation.contributions:
            # Handle creations from unclaimed fingerprinting identification:
            # allocate complete amount to creation artist
            result[creation.artist] = amount
            return result

        composer = [
            c.artist for c in creation.contributions
            if c.type == 'composition']
        texter = [
            c.artist for c in creation.contributions if c.type == 'text']
        performers = [
            c.artist for c in creation.contributions
            if c.type == 'performance']
        creators = composer or texter

        performer_amount = Decimal(0)
        composer_amount = Decimal(0)
        texter_amount = Decimal(0)

        if performers and creators:
            amount = amount / Decimal(2)

        if composer and texter:
            composer_amount = (
                amount * Decimal(65) / Decimal(100) / Decimal(len(composer)))
            texter_amount = (
                amount * Decimal(35) / Decimal(100) / Decimal(len(texter)))
        elif texter:
            texter_amount = amount / Decimal(len(texter))
        elif composer:
            composer_amount = amount / Decimal(len(composer))

        if performers:
            performer_amount = amount / Decimal(len(performers))

        for c in composer:
            result[c] += composer_amount
        for t in texter:
            result[t] += texter_amount
        for p in performers:
            result[p] += performer_amount

        # Traverse Originators
        # for original in creation.original_creations:
        #     if not original.derivative_type:
        #         result = self._allocate(
        #             creation=original.original_creation,
        #             amount=amount / Decimal(
        #                 len(creation.original_creations)),
        #             result=result)

        return result


##############################################################################
# Licenser
##############################################################################


class License(ModelSQL, ModelView, CurrentState, PublicApi):
    'License'
    __name__ = 'license'
    _history = True
    name = fields.Char('Name', required=True, select=True)
    code = fields.Char('Code', required=True, select=True)
    freedom_rank = fields.Integer('Freedom Rank')
    version = fields.Char('Version', required=True, select=False)
    country = fields.Char('Country', required=True, select=False)
    link = fields.Char('Link', required=True, select=False)

    @classmethod
    def __setup__(cls):
        super(License, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the license must be unique.')
        ]
        cls._order.insert(1, ('freedom_rank', 'ASC'))

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @classmethod
    def copy(cls, licenses, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(License, cls).copy(licenses, default=default)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('name',) + tuple(clause[1:]),
        ]


class Artist(ModelSQL, ModelView, EntityOrigin, AccessControlList, PublicApi,
             CurrentState, ClaimState, CommitState):
    'Artist'
    __name__ = 'artist'
    _history = True
    name = fields.Char(
        'Name', required=True, select=True, states=STATES, depends=DEPENDS)
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='The unique code of the artist')
    party = fields.Many2One(
        'party.party', 'Party', states=STATES, depends=DEPENDS,
        help='The legal person or organization acting the artist')
    group = fields.Boolean(
        'Group', states={
            'readonly': Or(
                ~Eval('active'),
                Bool(Eval('group_artists')),
                Bool(Eval('solo_artists')),
            ),
        }, depends=DEPENDS + ['group_artists', 'solo_artists'],
        help='Check, if artist is a group of other artists, '
        'otherwise the artist is a solo artist')
    solo_artists = fields.Many2Many(
        'artist-artist', 'group_artist', 'solo_artist',
        'Solo Artists', domain=[
            ('group', '=', False),
        ], states={
            'readonly': ~Eval('active'),
            'invisible': ~Eval('group'),
        }, depends=['active', 'group'],
        help='The membering solo artists of this group')
    group_artists = fields.Many2Many(
        'artist-artist', 'solo_artist', 'group_artist',
        'Group Artists', domain=[
            ('group', '=', True),
        ], states={
            'readonly': ~Eval('active'),
            'invisible': Bool(Eval('group')),
        }, depends=['active', 'group'],
        help='The groups this solo artist is member of')
    releases = fields.Many2Many(
        'artist-release', 'artist', 'release', 'Releases',
        help='The releases, which belongs to the artist')
    creations = fields.One2Many(
        'creation', 'artist', 'Creations', states=STATES,
        depends=DEPENDS, help='The creations, which belong to the artist.')
    # TODO: remove access_parties, change payee workflow to acl
    access_parties = fields.Function(
        fields.Many2Many(
            'party.party', None, None, 'Access Parties',
            help='Shows the collection of all parties with access '
            'permissions to this artist'),
        'get_access_parties')
    invitation_token = fields.Char(
        'Invitation Token', help='The invitation token of a web user '
        'to claim this artist')
    description = fields.Text(
        'Description', states=STATES, depends=DEPENDS,
        help='A description of the artist')
    picture_data = fields.Binary(
        'Picture Data', states=STATES, depends=DEPENDS,
        help='Picture data of a photograph or logo')
    picture_data_md5 = fields.Char(
        'Picture Data Hash', states=STATES, depends=DEPENDS,
        help='The md5 hash of the picture data, also acting as ressouce name.')
    picture_thumbnail_data = fields.Binary(
        'Thumbnail Data', states=STATES, depends=DEPENDS,
        help='Thumbnail data of the picture')
    picture_data_mime_type = fields.Char(
        'Picture Data Mime Type', states=STATES, depends=DEPENDS,
        help='The mime type of picture data.')
    payee = fields.Many2One(
        'party.party', 'Payee',
        domain=[('id', 'in', Eval('access_parties', []))],
        states={
            'readonly': Or(
                ~Eval('active'),
                Bool(Eval('valid_payee')),
            ),
        },
        depends=DEPENDS + ['access_parties', 'group_artists', 'solo_artists'],
        help='The actual payee party of this artist which is '
        'responsible for earnings of this artist')
    payee_proposal = fields.Many2One(
        'party.party', 'Payee Proposal',
        domain=[('id', 'in', Eval('access_parties', []))],
        states=STATES, depends=DEPENDS + ['access_parties'],
        help='The proposed payee party of this artist')
    payee_acceptances = fields.Many2Many(
        'artist.payee.acceptance', 'artist', 'party', 'Payee Acceptances',
        domain=[('id', 'in', Eval('access_parties', []))],
        states=STATES, depends=DEPENDS + ['access_parties'],
        help='The parties which accepts the payee proposal')
    valid_payee = fields.Boolean(
        'Valid Payee', states={
            'readonly': Or(
                ~Eval('active'),
                ~Bool(Eval('payee')),
            ),
        }, depends=DEPENDS + ['payee'],
        help='Check, if the payee is manually validated by '
        'administration.')
    bank_account_number = fields.Many2One(
        'bank.account.number', 'Bank Account Number', states={
            'readonly': ~Bool(Eval('payee'))},
        domain=[('id', 'in', Eval('bank_account_numbers'))],
        depends=['payee', 'bank_account_numbers'],
        help='The bank account number for this artist')
    bank_account_numbers = fields.Function(
        fields.Many2Many(
            'bank.account.number', None, None, 'Bank Account Numbers',
            help='Shows the collection of all available bank account '
            'numbers of this artist'),
        'on_change_with_bank_account_numbers')
    bank_account_owner = fields.Function(
        fields.Many2One(
            'party.party', 'Bank Account Owner', states={
                'readonly': (
                    Or(
                        ~Bool(Eval('payee')),
                        ~Bool(Eval('bank_account_number'))))},
            help='Shows the bank account owner for this artist',
            depends=['payee', 'bank_account_number']),
        'on_change_with_bank_account_owner')
    identifier = fields.One2Many('artist.identifier',
        'artist', '3rd-party identifier',
        states=STATES, depends=DEPENDS)

    @classmethod
    def __setup__(cls):
        super(Artist, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the Artist must be unique.'),
            ('invitation_token_uniq', 'UNIQUE(invitation_token)',
                'The invitation token of the artist must be unique.'),
        ]
        cls._error_messages.update(
            {
                'wrong_name': (
                    'Invalid Artist name "%%s": You can not use '
                    '"%s" in name field.' % SEPARATOR),
            })
        cls._order.insert(1, ('name', 'ASC'))

    @staticmethod
    def default_invitation_token():
        return str(uuid.uuid4())

    @classmethod
    def validate(cls, artists):
        super(Artist, cls).validate(artists)
        for artist in artists:
            artist.check_name()

    def check_name(self):
        if SEPARATOR in self.name:
            self.raise_user_error('wrong_name', (self.name,))

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @staticmethod
    def default_payee_validation_state():
        return 'accepted'

    @classmethod
    def get_access_parties(cls, artists, name):
        parties = {}
        for artist in artists:
            parties[artist.id] = []
            if artist.party:
                parties[artist.id] += [artist.party.id]
            if artist.solo_artists:
                for solo_artist in artist.solo_artists:
                    if solo_artist.party:
                        parties[artist.id] += [solo_artist.party.id]
        return parties

    @fields.depends('party', 'access_parties')
    def on_change_with_bank_account_numbers(self, name=None):
        BankAccountNumber = Pool().get('bank.account.number')

        bank_account_numbers = []
        numbers = BankAccountNumber.search(
            [
                'OR', [
                    (
                        'account.owner.id', 'in', [
                            p.id for p in self.access_parties])
                ], [
                    ('account.owner.id', '=', self.party.id
                        if self.party else None),
                ]
            ])
        if numbers:
            bank_account_numbers = [n.id for n in numbers]
        else:
            bank_account_numbers = None
        return bank_account_numbers

    @fields.depends('bank_account_number')
    def on_change_with_bank_account_owner(self, name=None):
        if self.bank_account_number:
            bank_account_owner = (
                self.bank_account_number.account.owner.id)
        else:
            bank_account_owner = None
        return bank_account_owner

    @fields.depends('payee')
    def on_change_payee(self):
        changes = {}
        if self.payee:
            changes = {
                'payee_acceptances': {
                    'remove': [
                        a.id for a in self.payee_acceptances]},
                'valid_payee': False,
                'payee_proposal': None}
        return changes

    @fields.depends('payee_proposal')
    def on_change_payee_proposal(self):
        changes = {}
        if self.payee_proposal:
            changes = {
                'payee_acceptances': {
                    'remove': [
                        a.id for a in self.payee_acceptances]},
                'valid_payee': False}
        return changes

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')
        default_roles = [('add', [
            r.id for r in
            AccessRole.search([('name', 'in', DEFAULT_ACCESS_ROLES)])])]

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            # autocreate sequence
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(config.artist_sequence.id)

        acls = {}
        elist = super(Artist, cls).create(vlist)
        for entry in elist:
            if entry.acl:
                continue
            # only normally created artists
            if entry.entity_origin != 'direct':
                continue
            # solo
            if entry.party and entry.party.web_user:
                acls[entry.party.web_user.id] = {
                    'entity': str(entry),
                    'web_user': entry.party.web_user.id,
                    'roles': default_roles
                }
            # group
            for member in entry.solo_artists:
                if not member.party or not member.party.web_user:
                    continue
                acls[member.party.web_user.id] = {
                    'entity': str(entry),
                    'web_user': member.party.web_user.id,
                    'roles': default_roles
                }
            # always autocreate creator acl
            if entry.entity_creator and entry.entity_creator.web_user:
                acls[entry.entity_creator.web_user.id] = {
                    'entity': str(entry),
                    'web_user': entry.entity_creator.web_user.id,
                    'roles': default_roles
                }
        AccessControlEntry.create(list(acls.values()))

        return elist

    @classmethod
    def copy(cls, artists, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(Artist, cls).copy(artists, default=default)

    @classmethod
    def write(cls, *args):
        default_roles = [('add', [
            r.id for r in
            AccessRole.search([('name', 'in', DEFAULT_ACCESS_ROLES)])])]
        actions = iter(args)
        args = []
        for artists, values in zip(actions, actions):
            for artist in artists:
                remaining = []
                for action, member_ids in values.get('solo_artists', []):
                    # add aces if not present
                    if action == 'add':
                        for member_id in member_ids:
                            member = cls.search([('id', '=', member_id)])
                            if not member:
                                continue
                            member = member[0]
                            if not member.party or not member.party.web_user:
                                continue
                            remaining.append(member.party.web_user.id)
                            ace = AccessControlEntry.search([
                                ('entity', '=', str(artist)),
                                ('web_user', '=', member.party.web_user)])
                            if ace:  # keep existing ace
                                continue
                            AccessControlEntry.create([{
                                'entity': str(artist),
                                'web_user': member.party.web_user.id,
                                'roles': default_roles}])
                    # remove existing aces
                    if action == 'remove':
                        for member_id in member_ids:
                            member = cls.search([('id', '=', member_id)])
                            if not member:
                                continue
                            member = member[0]
                            if not member.party or not member.party.web_user:
                                continue
                            # prevent deletion for members with same web_user
                            if member.party.web_user.id in remaining:
                                continue
                            ace = AccessControlEntry.search([
                                ('entity', '=', str(artist)),
                                ('web_user', '=', member.party.web_user)])
                            if not ace:
                                continue
                            AccessControlEntry.delete(ace)
            args.extend((artists, values))
        super(Artist, cls).write(*args)

    @classmethod
    def delete(cls, records):
        for record in records:
            if record.group or record.solo_artists:
                record.solo_artists = []
                record.save()
        return super(Artist, cls).delete(records)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('name',) + tuple(clause[1:]),
        ]


class ArtistArtist(ModelSQL):
    'Artist - Artist'
    __name__ = 'artist-artist'
    _history = True
    group_artist = fields.Many2One(
        'artist', 'Group Artist', required=True, select=True,
        ondelete='CASCADE')
    solo_artist = fields.Many2One(
        'artist', 'Solo Artist', required=True, select=True,
        ondelete='CASCADE')


class ArtistRelease(ModelSQL):
    'ArtistRelease'
    __name__ = 'artist-release'
    _history = True
    artist = fields.Many2One(
        'artist', 'Artist', required=True, select=True, ondelete='CASCADE')
    release = fields.Many2One(
        'release', 'Release', required=True, select=True, ondelete='CASCADE')


class ArtistPayeeAcceptance(ModelSQL):
    'Artist Payee Acceptance'
    __name__ = 'artist.payee.acceptance'
    _history = True
    artist = fields.Many2One(
        'artist', 'Artist', required=True, select=True, ondelete='CASCADE')
    party = fields.Many2One(
        'party.party', 'Party', required=True, select=True, ondelete='CASCADE')


class ArtistIdentifier(ModelSQL, ModelView, MixinIdentifier):
    'Artist Identifier'
    __name__ = 'artist.identifier'
    _history = True
    identifier_name = fields.Many2One('artist.identifier.name', 'ArtistIdentifierName', required=True, select=True, ondelete='CASCADE')
    artist = fields.Many2One('artist', 'Artist', required=True, select=True, ondelete='CASCADE')


class ArtistIdentifierName(ModelSQL, ModelView):
    'Artist Identifier Name'
    __name__ = 'artist.identifier.name'
    _history = True
    official_name = fields.Char('official name')
    version = fields.Char('version')


class Creation(ModelSQL, ModelView, EntityOrigin, AccessControlList, PublicApi,
               CurrentState, ClaimState, CommitState):
    'Creation'
    __name__ = 'creation'
    _history = True
    title = fields.Char(
        'Title', required=True, states=STATES, depends=DEPENDS,
        help='The abstract title of the creation, needed to identify '
        'it later as a track within a release, for example.')
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='The identification code for the creation')
    artist = fields.Many2One(
        'artist', 'Artist', states=STATES, depends=DEPENDS, help='The named '
        'artist for the creation')
    contributions = fields.One2Many(
        'creation.contribution', 'creation', 'Contributions', states=STATES,
        depends=DEPENDS, help='All individual contributions to the creation '
        'like composition and lyric creators, band members and singer/solo '
        'artists and their role.')
    lyrics = fields.Text(
        'Lyrics', help='The lyrics of the creation.')
    licenses = fields.Function(
        fields.Many2Many(
            'release.track', 'creation', 'license', 'Licenses'),
        'get_licenses')
    license = fields.Function(
        fields.Many2One('license', 'Default License'),
        'get_license', searcher='search_license')
    identifiers = fields.One2Many(
        'creation.identification', 'creation', 'Identifiers',
        states=STATES, depends=DEPENDS)
    derivative_relations = fields.One2Many(
        'creation.original.derivative', 'original_creation',
        'Derived Relations', states=STATES, depends=DEPENDS,
        help='All creations deriving from the actual creation')
    original_relations = fields.One2Many(
        'creation.original.derivative', 'derivative_creation',
        'Originating Relations', states=STATES, depends=DEPENDS,
        help='All creations originating the actual creation')
    releases = fields.One2Many(
        'release.track', 'creation', 'Releases',
        help='The releases of this creation.')
    release = fields.Function(
        fields.Many2One('release', 'First Release'),
        'get_release', searcher='search_release')
    genres = fields.Function(
        fields.Many2Many(
            'release-genre', 'release', 'genre', 'Genres',
            help='Shows the collection of all genres of all releases'),
        'get_genres')
    styles = fields.Function(
        fields.Many2Many(
            'release-style', 'release', 'style', 'Styles',
            help='Shows the collection of all styles of all releases'),
        'get_styles', searcher='search_styles')
    content = fields.One2Many(
        'content', 'creation', 'Content',
        help='Content associated with the creation.')
    tariff_categories = fields.One2Many(
        'creation-tariff_category', 'creation', 'Tariff Category',
        help='Tariff categories of the creation.')
    tariff_categories_list = fields.Function(
        fields.Char('Tariff Category List'),
        'on_change_with_tariff_categories_list')
    identifier = fields.One2Many('creation.identifier',
        'creation', '3rd-party identifier',
        states=STATES, depends=DEPENDS)

    @fields.depends('tariff_categories')
    def on_change_with_tariff_categories_list(self, name=None):
        tariff_categories = ''
        for tariff_category in self.tariff_categories:
            tariff_categories += '%s, ' % tariff_category.category.code
        return tariff_categories.rstrip(', ')

    @classmethod
    def __setup__(cls):
        super(Creation, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the creation must be unique.')
        ]

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    def get_rec_name(self, name):
        result = '[%s] %s' % (
            self.artist.name if self.artist and self.artist.name
            else '<unknown artist>',
            self.title)
        return result

    def get_licenses(self, name):
        licenses = []
        for releasecreation in self.releases:
            if releasecreation.license:
                licenses.append(releasecreation.license.id)
        return list(set(licenses))

    def get_license(self, name):
        license = None
        for l in self.licenses:
            if not license or l.freedom_rank > license.freedom_rank:
                license = l
        return license and license.id or None

    def get_release(self, name):
        release = None
        earliest_date = None
        for releasecreation in self.releases:
            current_release = releasecreation.release
            online_date = current_release.online_release_date
            physical_date = current_release.release_date
            if not earliest_date:
                release = current_release
                if not online_date or physical_date < online_date:
                    earliest_date = physical_date
                else:
                    earliest_date = online_date
            if physical_date and physical_date < earliest_date:
                earliest_date = physical_date
                release = current_release
            if online_date and online_date < earliest_date:
                earliest_date = online_date
                release = current_release
        return release and release.id or None

    def get_genres(self, name):
        genres = []
        for releasecreation in self.releases:
            for genre in releasecreation.release.genres:
                if genre.id not in genres:
                    genres.append(genre.id)
        return genres

    def get_styles(self, name):
        styles = []
        for releasecreation in self.releases:
            for style in releasecreation.release.styles:
                if style.id not in styles:
                    styles.append(style.id)
        return styles

    def search_license(self, name):
        return self.get_license(name)

    def search_release(self, name):
        return self.get_release(name)

    def search_genres(self, name):
        return self.get_genres(name)

    def search_styles(self, name):
        return self.get_styles(name)

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')
        default_roles = [('add', [
            r.id for r in
            AccessRole.search([('name', 'in', DEFAULT_ACCESS_ROLES)])])]

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(config.creation_sequence.id)

        acls = {}
        elist = super(Creation, cls).create(vlist)
        for entry in elist:
            if entry.acl:
                continue
            # always autocreate creator acl
            if entry.entity_creator and entry.entity_creator.web_user:
                acls[entry.entity_creator.web_user.id] = {
                    'entity': str(entry),
                    'web_user': entry.entity_creator.web_user.id,
                    'roles': default_roles
                }
        AccessControlEntry.create(list(acls.values()))

        return elist

    @classmethod
    def copy(cls, creations, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(Creation, cls).copy(creations, default=default)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('title',) + tuple(clause[1:]),
        ]

    def permits(self, web_user, code, derive=True):
        if super(Creation, self).permits(web_user, code, derive):
            return True
        if not derive:
            return False
        derivation = {
            'view_creation':   'view_artist_creations',
            'edit_creation':   'edit_artist_creations',
            'delete_creation': 'delete_artist_creations',
        }
        if self.artist:
            for ace in self.artist.acl:
                if ace.web_user != web_user:
                    continue
                for role in ace.roles:
                    for permission in role.permissions:
                        if permission.code == derivation[code]:
                            return True
        return False

    def permissions(self, web_user, valid_codes=False, derive=True):
        direct_permissions = super(Creation, self).permissions(
            web_user, valid_codes, derive)
        if not derive:
            return direct_permissions
        derivation = {
            'view_artist_creations':   'view_creation',
            'edit_artist_creations':   'edit_creation',
            'delete_artist_creations': 'delete_creation',
        }
        if not set(valid_codes).intersection(set(derivation.values())):
            return direct_permissions
        permissions = set(direct_permissions)
        if self.artist:
            for ace in self.artist.acl:
                if not derivation:
                    continue
                if ace.web_user != web_user:
                    continue
                for role in ace.roles:
                    for permission in role.permissions:
                        if permission.code in derivation:
                            permissions.add(derivation[permission.code])
                            del derivation[permission.code]
            if valid_codes:
                permissions = permissions.intersection(valid_codes)
        return tuple(permissions)


class CreationDerivative(ModelSQL, ModelView, PublicApi):
    'Creation - Original - Derivative'
    __name__ = 'creation.original.derivative'
    _history = True

    original_creation = fields.Many2One(
        'creation', 'Original Creation', select=True, required=True,
        ondelete='CASCADE')
    derivative_creation = fields.Many2One(
        'creation', 'Derivative Creation', select=True, required=True,
        ondelete='CASCADE')
    allocation_type = fields.Selection(
        [
            (None, ''),
            ('adaption', 'Adaption'),
            ('cover', 'Cover'),
            ('remix', 'Remix'),
        ], 'Allocation Type', required=True, sort=False,
        help='The allocation type of the actual creation in the relation '
        'from its origins or towards its derivatives\n'
        '*Adaption*: \n'
        '*Cover*: \n'
        '*Remix*: \n')


class CreationContribution(ModelSQL, ModelView, PublicApi):
    'Creation Contribution'
    __name__ = 'creation.contribution'
    _history = True

    creation = fields.Many2One(
        'creation', 'Creation', required=True, select=True,
        ondelete='CASCADE')
    artist = fields.Many2One(
        'artist', 'Artist', help='The involved artist contributing to the '
        'creation', ondelete='CASCADE')
    type = fields.Selection(
        [
            ('performance', 'Performance'),
            ('composition', 'Composition'),
            ('text', 'Text'),
        ], 'Type', required=True,
        help='The type of contribution of the artist.\n\n'
        '*performer*: The artist contributes a performance.\n'
        '*composer*: The artist contributes a composition.\n'
        '*text*: The artist contributes text.')
    performance = fields.Selection(
        [
            (None, ''),
            ('recording', 'Recording'),
            ('producing', 'Producing'),
            ('mastering', 'Mastering'),
            ('mixing', 'Mixing'),
        ], 'Performance', depends=['type'], states={
            'required': Eval('type') == 'performance',
            'invisible': Eval('type') != 'performance'},
        help='The type of performance of the performer.\n\n'
        '*recording*: Recoding of voice or instruments for the creation.\n'
        '*producing*: Producing of the creation.\n'
        '*mastering*: Mastering of the creation.\n'
        '*mixing*: Mixing of the creation')
    collecting_society = fields.Many2One(
        'collecting_society', 'Collecting Society',
        domain=[('represents_copyright', '=', True)],
        states={'invisible': Eval('type') != 'text'}, depends=['type'])
    neighbouring_rights_society = fields.Many2One(
        'collecting_society', 'Neighbouring Rights Society',
        domain=[('represents_ancillary_copyright', '=', True)],
        states={'invisible': Eval('type') != 'performance'}, depends=['type'])
    roles = fields.Many2Many(
        'creation.contribution-creation.role', 'contribution', 'role',
        'Roles',
        help='The roles the artist takes in this creation')
    roles_list = fields.Function(
        fields.Char('Roles List'), 'on_change_with_roles_list')

    # TODO: still needed? reason?
    # composition_copyright_date = fields.Date(
    #     'Composition Copyright Date')
    # composition_copyright_owner = fields.Many2One(
    #     'party.party', 'Composition Copyright Owner')
    # composition_license = fields.Many2One(
    #     'license', 'License')
    # composition_publishing_date = fields.Date(
    #     'Composition Publishing Date')
    # composition_publisher = fields.Many2One(
    #     'party.party', 'Composition Publisher',
    #     help='Composition Publishing Entity')
    # lyrics_copyright_date = fields.Date(
    #     'Lyrics Copyright Date')
    # lyrics_copyright_artist = fields.Many2One(
    #     'artist', 'Lyrics Copyright Artist')
    # lyrics_license = fields.Many2One(
    #     'license', 'License')
    # lyrics_publishing_date = fields.Date(
    #     'Lyrics Publishing Date')
    # lyrics_publisher = fields.Many2One(
    #     'party.party', 'Lyrics Publisher', help='Lyrics Publishing Entity')

    @fields.depends('roles')
    def on_change_with_roles_list(self, name=None):
        roles = ''
        for role in self.roles:
            roles += '%s, ' % role.name
        return roles.rstrip(', ')

    def get_rec_name(self, name):
        result = '[%s] %s' % (
            self.type, self.creation.title)
        return result


class CreationContributionRole(ModelSQL, ModelView):
    'Creation Contribution - Creation Role'
    __name__ = 'creation.contribution-creation.role'
    _history = True

    contribution = fields.Many2One(
        'creation.contribution', 'Contribution', required=True, select=True,
        ondelete='CASCADE')
    role = fields.Many2One(
        'creation.role', 'Role', required=True, select=True,
        ondelete='CASCADE')


class CreationRole(ModelSQL, ModelView, EntityOrigin, PublicApi):
    'Creation Role'
    __name__ = 'creation.role'
    _history = True

    name = fields.Char(
        'Name', required=True, translate=True, help='The name of the role')
    description = fields.Text(
        'Description', translate=True, help='The description of the role')


class CreationTariffCategory(ModelSQL, ModelView, PublicApi):
    'Creation - Tariff Category'
    __name__ = 'creation-tariff_category'
    _history = True

    creation = fields.Many2One(
        'creation', 'Creation', required=True, ondelete='CASCADE')
    category = fields.Many2One(
        'tariff_system.category', 'Category', required=True,
        ondelete='CASCADE')

    collecting_society = fields.Many2One(
        'collecting_society', 'Collecting Society', ondelete='CASCADE')


class CreationIdentifier(ModelSQL, ModelView, MixinIdentifier):
    'Creation Identifier'
    __name__ = 'creation.identifier'
    _history = True
    identifier_name = fields.Many2One('creation.identifier.name', 'CreationIdentifierName', required=True, select=True, ondelete='CASCADE')
    creation = fields.Many2One('creation', 'Creation', required=True, select=True, ondelete='CASCADE')


class CreationIdentifierName(ModelSQL, ModelView):
    'Creation Identifier Name'
    __name__ = 'creation.identifier.name'
    _history = True
    official_name = fields.Char('official name')
    version = fields.Char('version')


class Release(ModelSQL, ModelView, EntityOrigin, AccessControlList, PublicApi,
              CurrentState, ClaimState, CommitState):
    'Release'
    __name__ = 'release'
    _history = True
    _rec_name = 'title'

    type = fields.Selection(
        [
            ('artist', 'Artist Release'),
            ('split', 'Split Release'),
            ('compilation', 'Compilation'),
        ], 'Release Type', required=True, help='The release type:\n\n'
        '*Artist Release*: The release belongs to one or more artists '
        '(artist/split album).\n'
        '*Compilation*: The release belongs to the producer of the '
        'compilation and usually contains various artists.')

    # artists
    artists = fields.Many2Many(
        'artist-release', 'release', 'artist', 'Artists',
        help='The artists, to which the release belongs.',
        states={
            'required': Eval('type') == 'artist',
            'invisible': Eval('type') != 'artist',
        }, depends=['type'])
    artists_list = fields.Function(
        fields.Char('Artists List'), 'on_change_with_artists_list')

    # tracks
    tracks = fields.One2Many(
        'release.track', 'release', 'Creations',
        help='The tracks of the release')
    # medium_numbers = fields.Function(
    #     fields.Many2Many(
    #         'release.track', 'creation', 'release', 'Media Numbers',
    #         help='Media numbers of the Release.'),
    #     'get_medium_numbers')

    # metadata
    title = fields.Char('Title')
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='The identification code for the release')
    picture_data = fields.Binary(
        'Picture Data', states=STATES, depends=DEPENDS,
        help='Picture data of a photograph or logo')
    picture_data_md5 = fields.Char(
        'Picture Data Hash', states=STATES, depends=DEPENDS,
        help='The md5 hash of the picture data, also acting as ressouce name.')
    picture_thumbnail_data = fields.Binary(
        'Thumbnail Data', states=STATES, depends=DEPENDS,
        help='Thumbnail data of the picture')
    picture_data_mime_type = fields.Char(
        'Picture Data Mime Type', states=STATES, depends=DEPENDS,
        help='The mime type of picture data.')
    genres = fields.Many2Many(
        'release-genre', 'release', 'genre', 'Genres',
        help='The genres of the release.')
    styles = fields.Many2Many(
        'release-style', 'release', 'style', 'Styles',
        help='The styles of the release.')
    warning = fields.Char(
        'Warning', help='A warning note for this release.')

    # production
    isrc_code = fields.Char(
        'ISRC Code',
        help='The International Standard Recording Code of the release')
    copyright_date = fields.Date(
        'Copyright Date', help='Date of the copyright.')
    # copyright_owners = fields.Many2One(
    #    'party.party', 'Copyright Owner(s)', help='Copyright owning parties')
    production_date = fields.Date(
        'Production Date', help='Date of production.')
    producers = fields.Function(
        fields.Many2Many(
            'creation.contribution', 'creation', 'artist', 'Producer(s)',
            help='Producers involved in the creations of the release.'),
        'get_producers')

    # distribution
    ean_upc_code = fields.Char('EAN/UPC Code', help='The EAN/UPC Code')
    release_date = fields.Date('Release Date', help='Date of (first) release.')
    release_cancellation_date = fields.Date(
        'Release Cancellation Date', help='Date of release cancellation')
    online_release_date = fields.Date(
        'Online Release Date', help='The Date of digital online release.')
    online_cancellation_date = fields.Date(
        'Online Cancellation Date',
        help='Date of online release cancellation.')
    distribution_territory = fields.Char(
        'Distribution Territory')
    label = fields.Many2One(
        'label', 'Label', help='The label of the release.')
    label_catalog_number = fields.Char(
        'Label Catalog Number',
        help='The labels catalog number of the release.')
    publisher = fields.Many2One(
        'publisher', 'Publisher', help='The publisher of the release.')
    neighbouring_rights_societies = fields.Function(
        fields.Many2Many(
            'collecting_society', None, None, 'Neighbouring Rights Societies',
            help='Neighbouring Rights Societies involved in the creations of '
            'the release.'),
        'get_neighbouring_rights_societies')
    identifier = fields.One2Many('release.identifier',
        'release', '3rd-party identifier',
        states=STATES, depends=DEPENDS)

    @classmethod
    def __setup__(cls):
        super(Release, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the release must be unique.')
        ]
        cls._order.insert(1, ('title', 'ASC'))

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @staticmethod
    def default_state():
        return 'on_approval'

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')
        default_roles = [('add', [
            r.id for r in
            AccessRole.search([('name', 'in', DEFAULT_ACCESS_ROLES)])])]

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(config.release_sequence.id)

        acls = {}
        elist = super(Release, cls).create(vlist)
        for entry in elist:
            if entry.acl:
                continue
            # always autocreate creator acl
            if entry.entity_creator and entry.entity_creator.web_user:
                acls[entry.entity_creator.web_user.id] = {
                    'entity': str(entry),
                    'web_user': entry.entity_creator.web_user.id,
                    'roles': default_roles
                }
        AccessControlEntry.create(list(acls.values()))

        return elist

    @classmethod
    def copy(cls, releases, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(Release, cls).copy(releases, default=default)

    @classmethod
    def delete(cls, records):
        for record in records:
            if record.genres:
                record.genres = []
                record.save()
        return super(Release, cls).delete(records)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('title',) + tuple(clause[1:]),
        ]

    @fields.depends('artists')
    def on_change_with_artists_list(self, name=None):
        artists = []
        for artist in self.artists:
            artists.append(artist.name)
        return ", ".join(artists)

    # tried to get a nice ordered list of media
    # (see function field above)
    # def get_medium_numbers(self, name):
    #     medium_numbers = []
    #     for track in self.tracks:
    #             medium_numbers.append(track)
    #     return list(set(medium_numbers))

    def get_producers(self, name):
        producers = []
        for track in self.tracks:
            for contribution in track.creation.contributions:
                performance = (contribution.type == 'performance')
                producing = (contribution.performance == 'producing')
                if performance and producing:
                    producers.append(contribution.artist.id)
        return list(set(producers))

    def get_neighbouring_rights_societies(self, name):
        societies = []
        for track in self.tracks:
            for contribution in track.creation.contributions:
                performance = (contribution.type == 'performance')
                society = contribution.neighbouring_rights_society
                if performance and society:
                    societies.append(society.id)
        return list(set(societies))

    def permits(self, web_user, code, derive=True):
        if super(Release, self).permits(web_user, code, derive):
            return True
        if not derive:
            return False
        derivation = {
            'view_release':   'view_artist_releases',
            'edit_release':   'edit_artist_releases',
            'delete_release': 'delete_artist_releases',
        }
        for artist in self.artists:
            for ace in artist.acl:
                if ace.web_user != web_user:
                    continue
                for role in ace.roles:
                    for permission in role.permissions:
                        if permission.code == derivation[code]:
                            return True
        return False

    def permissions(self, web_user, valid_codes=False, derive=True):
        direct_permissions = super(Release, self).permissions(
            web_user, valid_codes, derive)
        if not derive:
            return direct_permissions
        derivation = {
            'view_artist_releases':   'view_release',
            'edit_artist_releases':   'edit_release',
            'delete_artist_releases': 'delete_release',
        }
        if not set(valid_codes).intersection(set(derivation.values())):
            return direct_permissions
        permissions = set(direct_permissions)
        for artist in self.artists:
            for ace in artist.acl:
                if not derivation:
                    continue
                if ace.web_user != web_user:
                    continue
                for role in ace.roles:
                    for permission in role.permissions:
                        if permission.code in derivation:
                            permissions.add(derivation[permission.code])
                            del derivation[permission.code]
            if valid_codes:
                permissions = permissions.intersection(valid_codes)
        return tuple(permissions)


class ReleaseTrack(ModelSQL, ModelView, PublicApi):
    'Release Track'
    __name__ = 'release.track'
    _history = True

    release = fields.Many2One(
        'release', 'Release', required=True, select=True, ondelete='CASCADE')
    creation = fields.Many2One(
        'creation', 'Creation', required=True, select=True, ondelete='CASCADE')

    title = fields.Char(
        'Title', select=True, states={'required': True},
        help='The title or name of the creation on the release')
    medium_number = fields.Integer(
        'Medium Number', help=u'The number of the medium on CD, LP, ...')
    track_number = fields.Integer(
        'Track Number', help='Track number on the medium')
    license = fields.Many2One(
        'license', 'License', help='License for the creation on the release')


class ReleaseGenre(ModelSQL, ModelView):
    'Release - Genre'
    __name__ = 'release-genre'
    _history = True

    release = fields.Many2One(
        'release', 'Release', required=True, select=True, ondelete='CASCADE')
    genre = fields.Many2One(
        'genre', 'Genre', required=True, select=True, ondelete='CASCADE')


class ReleaseStyle(ModelSQL, ModelView):
    'Release - Style'
    __name__ = 'release-style'
    _history = True

    release = fields.Many2One(
        'release', 'Release', required=True, select=True, ondelete='CASCADE')
    style = fields.Many2One(
        'style', 'Style', required=True, select=True, ondelete='CASCADE')


class ReleaseIdentifier(ModelSQL, ModelView, MixinIdentifier):
    'Release Identifier'
    __name__ = 'release.identifier'
    _history = True
    release_identifier_name = fields.Many2One(
        'release.identifier.name', 'Release Identifier Name', required=True, 
        select=True, ondelete='CASCADE')
    release = fields.Many2One(
        'release', 'Release', required=True, select=True, ondelete='CASCADE')


class ReleaseIdentifierName(ModelSQL, ModelView):
    'Release Identifier Name'
    __name__ = 'release.identifier.name'
    _history = True
    name = fields.Char('Name')
    version = fields.Char('Version')


class Genre(ModelSQL, ModelView, PublicApi):
    'Genre'
    __name__ = 'genre'
    _history = True

    name = fields.Char('Name', help='The name of the genre.')
    description = fields.Text(
        'Description', help='The description of the genre.')


class Style(ModelSQL, ModelView, PublicApi):
    'Style'
    __name__ = 'style'
    _history = True

    name = fields.Char('Name', help='The name of the style.')
    description = fields.Text(
        'Description', help='The description of the style.')


class Label(ModelSQL, ModelView, EntityOrigin, PublicApi, CurrentState):
    'Label'
    __name__ = 'label'
    _history = True

    name = fields.Char('Name', help='The name of the label.')
    party = fields.Many2One(
        'party.party', 'Party', help='The legal party of the label')
    gvl_code = fields.Char(
        'GVL Code', help='The label code of the german '
        '"Gesellschaft zur Verwertung von Leistungsschutzrechten" (GVL)')


class Publisher(ModelSQL, ModelView, EntityOrigin, PublicApi, CurrentState):
    'Publisher'
    __name__ = 'publisher'
    _history = True

    name = fields.Char('Name', help='The name of the publisher.')
    party = fields.Many2One(
        'party.party', 'Party', help='The legal party of the publisher')


##############################################################################
# Licensee
##############################################################################


class Utilisation(ModelSQL, ModelView):
    'Utilisation'
    __name__ = 'creation.utilisation'
    timestamp = fields.DateTime(
        'Timestamp', required=True, select=True,
        help='Point in time of utilisation')
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='Sequential code number of the utilisation')
    party = fields.Many2One(
        'party.party', 'Utiliser Party',
        help='The party who uses the creation')
    creation = fields.Many2One(
        'creation', 'Creation', required=True,
        help='The work which is used by the utilizer')
    allocation = fields.Many2One(
        'distribution.allocation', 'Allocation',
        help='The allocation of the utilisation')
    state = fields.Selection(
        [
            ('not_distributed', 'Not Distributed'),
            ('processing', 'Distribution in Process'),
            ('distributed', 'Distributed'),
        ], 'State', required=True, sort=False,
        help='The distribution state of the utilisation.\n\n'
        '*Not Distributed*: The default state for newly created '
        'utilisations.\n'
        '*Distribution in Process*: A distribution is in process.\n'
        '*Distributed*: The distribution is finished and an allocation is '
        'created')
    # origin = fields.Reference(
    #     'Origin', [
    #         ('creation.utilisation.imp', 'IMP')
    #     ],
    #     help='The originating data of the use')

    @classmethod
    def __setup__(cls):
        super(Utilisation, cls).__setup__()
        cls._sql_constraints = [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the utilisation must be unique.')
        ]
        cls._order.insert(1, ('timestamp', 'ASC'))

    @staticmethod
    def default_state():
        return 'not_distributed'

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    def get_rec_name(self, name):
        return '%s: %s' % (self.creation.title, self.creation.artist)

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(
                    config.utilisation_sequence.id)
        return super(Utilisation, cls).create(vlist)

    @classmethod
    def copy(cls, utilisations, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(Utilisation, cls).copy(utilisations, default=default)

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('timestamp',) + tuple(clause[1:]),
        ]


class Device(ModelSQL, ModelView):
    'Device'
    __name__ = 'device'
    _history = True
    _rec_name = 'uuid'
    web_user = fields.Many2One(
        'web.user', 'Web User', required=True)
    uuid = fields.Char(
        'UUID', required=True, help='The universally unique identifier '
        'of the device used by a web user')
    name = fields.Char(
        'Device Name', required=True,
        help='Name of the device, i.e. model name, operating system, etc.')
    os_name = fields.Char(
        'OS Name', help='Name of the operating system the tracker runs on')
    os_version = fields.Char(
        'OS Version',
        help='Version of the operating system the tracker runs on')
    software_name = fields.Char(
        'Software Name', help='Name of the tracker software')
    software_version = fields.Char(
        'Software Version', help='The version of the tracker software')
    software_vendor = fields.Char(
        'Software Vendor', help='Vendor of the tracker software')

    @classmethod
    def __setup__(cls):
        super(Device, cls).__setup__()
        cls._sql_constraints += [
            ('uuid', 'UNIQUE(uuid)',
                'The UUID of the device must be unique.'),
        ]

    @staticmethod
    def default_uuid():
        return str(uuid.uuid4())


class Identifier(ModelSQL, ModelView):
    'Identifier'
    __name__ = 'creation.identification.identifier'
    _history = True
    _rec_name = 'identifier'
    identification = fields.Many2One(
        'creation.identification', 'Identification',
        help='The identification of a creation for this identifier')
    identifier = fields.Text('Identifier')


class Identification(ModelSQL, ModelView):
    'Identification'
    __name__ = 'creation.identification'
    _history = True
    identifiers = fields.One2Many(
        'creation.identification.identifier', 'identification', 'Identifiers',
        help='The identifiers of the creation')
    creation = fields.Many2One(
        'creation', 'Creation', help='The creation identified by '
        'the identifiers')
    id3 = fields.Text('ID3', help='ID3 tag')

    def get_rec_name(self, name):
        return (self.creation.title if self.creation else 'unknown')


class Fingerprintlog(ModelSQL, ModelView):
    'Fingerprintlog'
    __name__ = 'content.fingerprintlog'
    _history = True
    content = fields.Many2One(
        'content', 'Content', required=True,
        help='The fingerprinted content.')
    user = fields.Many2One(
        'res.user', 'User', states={'required': True},
        help='The user which fingerprinted the content.')
    timestamp = fields.DateTime(
        'Timestamp', states={'required': True}, select=True,
        help='Point in time of fingerprinting')
    fingerprinting_algorithm = fields.Char(
        'Algorithm', states={'required': True},
        help='Fingerprinting mechanism of the content, e.g. echoprint')
    fingerprinting_version = fields.Char(
        'Version', states={'required': True},
        help='Fingerprinting algorithm version of the content')


##############################################################################
# Archive
##############################################################################


class Storehouse(ModelSQL, ModelView, CurrentState):
    'Storehouse'
    __name__ = 'storehouse'
    _rec_name = 'code'
    _history = True
    code = fields.Char(
        'Code', required=True,
        help='The Code of the Storehouse.')
    details = fields.Text(
        'Details', help='Details of the Storehouse.')
    user = fields.Many2One(
        'res.user', 'User', states={'required': True},
        help='The admin user of the Storehouse.')
    harddisks = fields.One2Many(
        'harddisk', 'storehouse', 'Harddisks',
        help='The harddisks in the Storehouse.')


class HarddiskLabel(ModelSQL, ModelView, CurrentState):
    'Harddisk Label'
    __name__ = 'harddisk.label'
    _rec_name = 'code'
    _history = True
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='The Label code for the Harddisk.')
    harddisks = fields.One2Many(
        'harddisk', 'label', 'Harddisks',
        help='The harddisks in the Storehouse.')

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(
                    config.harddisk_label_sequence.id)
        return super(HarddiskLabel, cls).create(vlist)

    @classmethod
    def copy(cls, harddisk_labels, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(HarddiskLabel, cls).copy(
            harddisk_labels, default=default)


class Harddisk(ModelSQL, ModelView, CurrentState):
    'Harddisk'
    __name__ = 'harddisk'
    _rec_name = 'uuid_harddisk'
    _history = True
    label = fields.Many2One(
        'harddisk.label', 'Label', states={'required': True},
        help='The Label of the Harddisk.')
    version = fields.Integer(
        'Version', states={'required': True},
        help='The version of a Harddisk Label in the Storehouse.')
    storehouse = fields.Many2One(
        'storehouse', 'Storehouse', states={'required': True},
        help='The Storehouse of the Harddisk.')
    location = fields.Char(
        'Location', help='The local position of the Harddisk.')
    closed = fields.Boolean(
        'Closed', help='The finalization state of the Harddisk.')
    raid_type = fields.Char(
        'Raid Type', states={'required': True}, help='The type of the Raid.')
    raid_number = fields.Char(
        'Raid Number', states={'required': True},
        help='The current number of the harddisk in the Raid.')
    raid_total = fields.Char(
        'Raid Total', states={'required': True},
        help='The total number of harddisks in the Raid.')
    filesystems = fields.One2Many(
        'harddisk.filesystem', 'harddisk', 'Filesystems',
        help='The Filesystems on the Harddisk.')
    uuid_host = fields.Char(
        'Uuid Host', states={'required': True}, help='The uuid of the Host.')
    uuid_harddisk = fields.Char(
        'Uuid Harddisk', states={'required': True},
        help='The uuid of the Harddisk.')
    checksum_harddisk = fields.Many2One(
        'checksum', 'Checksum Harddisk', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Harddisk.')
    tests = fields.One2Many(
        'harddisk.test', 'harddisk', 'Integrity Tests',
        help='The integrity tests of the Harddisk.')
    user = fields.Many2One(
        'res.user', 'User', states={'required': True},
        help='The admin user, who created the harddisk.')
    online = fields.Boolean(
        'Online', help='The online status of the harddisk.')
    state = fields.Selection(
        [
            ('setup', 'Setup'),
            ('in_use', 'In Use'),
            ('out_of_order', 'Out of Order'),
        ], 'State', required=True, sort=False,
        help='The usage state of the Harddisk.')
    # status = fields.Function(status of last harddisk test)
    # sticker_text = fields.Function(text of sticker with label, etc)
    # sticker_pdf = fields.Function(pdf of sticker with label, etc)


class HarddiskTest(ModelSQL, ModelView):
    'Harddisk Test'
    __name__ = 'harddisk.test'
    _history = True
    harddisk = fields.Many2One(
        'harddisk', 'Harddisk', required=True,
        help='The harddisk which was tested.')
    user = user = fields.Many2One(
        'res.user', 'User', states={'required': True},
        help='The admin user which executed the Test.')
    timestamp = fields.DateTime(
        'Timestamp', required=True,
        help='The point in time of the Test.')
    status = fields.Selection(
        [
            ('sane', 'Sane'),
            ('error_harddisk', 'Harddisk Error'),
            ('error_partition', 'Partition Error'),
            ('error_raid', 'Raid Error'),
            ('error_raid_sub', 'Raid Sub Error'),
            ('error_crypto', 'Crypto Error'),
            ('error_lvm', 'Lvm Error'),
            ('error_filesystem', 'Filesystem Error'),
        ], 'State', required=True, sort=False,
        help='The usage state of the Harddisk.')

    def get_rec_name(self, name):
        return self.harddisk.uuid_harddisk + "@" + str(self.timestamp)


class FilesystemLabel(ModelSQL, ModelView, CurrentState):
    'Filesystem Label'
    __name__ = 'harddisk.filesystem.label'
    _rec_name = 'code'
    _history = True
    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='The Label code for the Filesystem.')
    filesystems = fields.One2Many(
        'harddisk.filesystem', 'label', 'Filesystems',
        help='The Filesystems of the Filesystem Label.')
    contents = fields.One2Many(
        'content', 'filesystem_label', 'Contents',
        help='The Contents of the Filesystem Label.')

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(
                    config.filesystem_label_sequence.id)
        return super(FilesystemLabel, cls).create(vlist)

    @classmethod
    def copy(cls, filesystem_labels, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(FilesystemLabel, cls).copy(
            filesystem_labels, default=default)


class Filesystem(ModelSQL, ModelView, CurrentState):
    'Filesystem'
    __name__ = 'harddisk.filesystem'
    _rec_name = 'uuid_filesystem'
    _history = True
    label = fields.Many2One(
        'harddisk.filesystem.label', 'Label', states={'required': True},
        help='The Label of the Filesystem.')
    harddisk = fields.Many2One(
        'harddisk', 'Harddisk', states={'required': True},
        help='The Harddisk on which the filesystem resides.')
    closed = fields.Boolean(
        'Closed', help='The finalization state of the Filesystem.')
    partition_number = fields.Integer(
        'Partition Number', states={'required': True},
        help='The number of the partition on the Harddisk.')
    uuid_partition = fields.Char(
        'Uuid Partition', states={'required': True},
        help='The uuid of the Partition.')
    uuid_raid = fields.Char(
        'Uuid Raid', states={'required': True},
        help='The uuid of the Raid.')
    uuid_raid_sub = fields.Char(
        'Uuid Raid Sub', states={'required': True},
        help='The uuid of the Raid Sub.')
    uuid_crypto = fields.Char(
        'Uuid Crypto', states={'required': True},
        help='The uuid of the Crypto.')
    uuid_lvm = fields.Char(
        'Uuid Lvm', states={'required': True},
        help='The uuid of the Lvm.')
    uuid_filesystem = fields.Char(
        'Uuid Filesystem', states={'required': True},
        help='The uuid of the Filesystem.')
    checksum_partition = fields.Many2One(
        'checksum', 'Checksum', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Partition.')
    checksum_raid = fields.Many2One(
        'checksum', 'Checksum', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Raid.')
    checksum_raid_sub = fields.Many2One(
        'checksum', 'Checksum', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Raid Sub.')
    checksum_crypto = fields.Many2One(
        'checksum', 'Checksum', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Crypto.')
    checksum_lvm = fields.Many2One(
        'checksum', 'Checksum', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Lvm.')
    checksum_filesystem = fields.Many2One(
        'checksum', 'Checksum', states={
            'required': Bool(Eval('closed')),
        }, help='The Checksum of the Filesystem.')


class Content(ModelSQL, ModelView, EntityOrigin, AccessControlList, PublicApi,
              CurrentState, CommitState):
    'Content'
    __name__ = 'content'
    _rec_name = 'uuid'
    _history = True

    code = fields.Char(
        'Code', required=True, select=True, states={
            'readonly': True,
        }, help='The unique code of the content')
    uuid = fields.Char(
        'UUID', required=True, help='The uuid of the Content.')
    category = fields.Selection(
        [
            ('audio', 'Audio'),
            ('sheet', 'Sheet Music'),
        ], 'Category', required=True, help='The category of content.')
    creation = fields.Many2One(
        'creation', 'Creation', states=STATES, depends=DEPENDS,
        help='The creation associated with the content.')

    # --- FILES --------------------------------------------------------------

    # file metadata
    name = fields.Char(
        'File Name', help='The name of the file.')
    extension = fields.Function(
        fields.Char('Extension'), 'on_change_with_extension')
    size = fields.BigInteger(
        'Size', help='The size of the content in Bytes.')
    mime_type = fields.Char(
        'Mime Type', help='The media or content type.')
    checksums = fields.One2Many(
        'checksum', 'origin', 'Checksums',
        help='The checksums of the content.')

    # file processing
    path = fields.Char(
        'Path', states={
            'invisible': Eval('processing_state') == 'deleted'
        }, depends=['processing_state'])
    preview_path = fields.Char(
        'Preview Path', states={
            'invisible': Eval('processing_state') == 'deleted'
        }, depends=['processing_state'])
    filesystem_label = fields.Many2One(
        'harddisk.filesystem.label', 'Filesystem Label', states={
            'invisible': Eval('processing_state') != 'archived'
        }, depends=['processing_state'],
        help='The Filesystem Label of the Content.')
    processing_state = fields.Selection(
        [
            (None, ''),
            ('uploaded', 'Upload finished'),
            ('previewed', 'Preview created'),
            ('checksummed', 'Checksum created'),
            ('fingerprinted', 'Fingerprint created'),
            ('dropped', 'Dropped'),
            ('archived', 'Archived'),
            ('deleted', 'Deleted'),
            ('rejected', 'Rejected'),
            ('unknown', 'Unknown'),
        ], 'State', states={'required': True},
        help='The processing state of the content.')
    processing_hostname = fields.Char(
        'Processor', states={
            'invisible': Or(
                Eval('processing_state') == 'deleted',
                Eval('processing_state') == 'archived'
            )
        }, depends=['processing_state'],
        help='The hostname of the processing machine.')
    storage_hostname = fields.Char(
        'Storage', states={
            'invisible': Or(
                Eval('processing_state') == 'deleted',
                Eval('processing_state') == 'archived'
            )
        }, depends=['processing_state'],
        help='The hostname of the storage machine.')
    rejection_reason = fields.Selection(
        [
            (None, ''),
            ('checksum_collision', 'Duplicate Checksum'),
            ('fingerprint_collision', 'Duplicate Fingerprint'),
            ('format_error', 'Format Error'),
            ('no_fingerprint', 'No Fingerprint'),
            ('lossy_compression', 'Lossy Compression'),
            ('missing_database_record', 'Missing Database Record'),
        ], 'Reason', states={
            'invisible': Eval('processing_state') != 'rejected',
            'required': Eval('processing_state') == 'rejected'
        }, depends=['processing_state'],
        help='The reason of the rejection.')
    rejection_reason_details = fields.Text(
        'Details', help='Rejection Explanation',
        states={'invisible': Eval('processing_state') != 'rejected'},
        depends=['processing_state'])
    duplicate_of = fields.Many2One(
        'content', 'Duplicate of',
        domain=[('duplicate_of', '=', None)],
        states={
            'invisible': And(
                Eval('rejection_reason') != 'checksum_collision',
                Eval('rejection_reason') != 'fingerprint_collision'
            ),
            'required': Or(
                Eval('rejection_reason') == 'checksum_collision',
                Eval('rejection_reason') == 'fingerprint_collision',
            )
        }, depends=['rejection_reason'],
        help='The original duplicated Content.')
    duplicates = fields.One2Many(
        'content', 'duplicate_of', 'Duplicates',
        domain=[
            (
                'rejection_reason', 'in',
                ['checksum_collision', 'fingerprint_collision']
            ),
        ], help='The original duplicated Content.')
    mediation = fields.Boolean('Mediation')

    # --- SHEET ---------------------------------------------------------------

    # --- AUDIO ---------------------------------------------------------------

    # low level audio metadata
    length = fields.Float(
        'Length', digits=(16, 6),
        help='The length or duration of the audio content in seconds [s].',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    channels = fields.Integer(
        'Channels', help='The number of sound channels.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    sample_rate = fields.Integer(
        'Sample Rate', help='Sample rate in Hertz [Hz][1/s].',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    sample_width = fields.Integer(
        'Sample Width', help='Sample width in Bits.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])

    # high level audio metadata
    metadata_artist = fields.Char(
        'Metadata Artist', help='Artist in uploaded metadata.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    metadata_title = fields.Char(
        'Metadata Title', help='Title in uploaded metadata.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    metadata_release = fields.Char(
        'Metadata Release', help='Release in uploaded metadata.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    metadata_release_date = fields.Char(
        'Metadata Release Date', help='Release date in uploaded metadata.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    metadata_track_number = fields.Char(
        'Metadata Track Number', help='Track number in uploaded metadata.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    fingerprintlogs = fields.One2Many(
        'content.fingerprintlog', 'content', 'Fingerprintlogs',
        help='The fingerprinting log for the content.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])

    # temporary data for analysis
    pre_ingest_excerpt_score = fields.Integer(
        'Pre Ingest Excerpt Score',
        help='Fingerprint match score before ingestion',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    post_ingest_excerpt_score = fields.Integer(
        'Post Ingest Excerpt Score',
        help='Fingerprint match score after ingestion',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    uniqueness = fields.Function(
        fields.Float(
            'Uniqueness',
            help='Ratio of fingerprint match score after/before ingestion',
            states={'invisible': Eval('category') != 'audio'},
            depends=['category']),
        'get_uniqueness')
    most_similiar_content = fields.Many2One(
        'content', 'Most Similiar Content',
        help='The most similiar content in our database.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    most_similiar_artist = fields.Char(
        'Most Similiar Artist', help='The most similiar artist in echoprint.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])
    most_similiar_track = fields.Char(
        'Most Similiar Track', help='The most similiar track in echoprint.',
        states={'invisible': Eval('category') != 'audio'},
        depends=['category'])

    @classmethod
    def __setup__(cls):
        super(Content, cls).__setup__()
        cls._order.insert(1, ('name', 'ASC'))
        cls._sql_constraints += [
            ('code_uniq', 'UNIQUE(code)',
             'The code of the Content must be unique.'),
            ('uuid_uniq', 'UNIQUE(uuid)',
                'The UUID of the content must be unique.'),
        ]

    @staticmethod
    def order_code(tables):
        table, _ = tables[None]
        return [CharLength(table.code), table.code]

    @staticmethod
    def default_category():
        return 'audio'

    @fields.depends('name')
    def on_change_with_extension(self, name=None):
        if self.name:
            return os.path.splitext(self.name)[1].lstrip('.')

    def get_uniqueness(self, name=None):
        minval = 0.0
        maxval = 100.0
        if not self.post_ingest_excerpt_score:
            return minval
        if not self.pre_ingest_excerpt_score:
            return maxval
        score = self.post_ingest_excerpt_score / float(
            self.pre_ingest_excerpt_score
        )
        return score if score <= maxval else maxval

    def get_currency_digits(self, name):
        Company = Pool().get('company.company')
        if Transaction().context.get('company'):
            company = Company(Transaction().context['company'])
            return company.currency.digits
        return 2

    @classmethod
    def create(cls, vlist):
        Sequence = Pool().get('ir.sequence')
        Configuration = Pool().get('collecting_society.configuration')
        default_roles = [('add', [
            r.id for r in
            AccessRole.search([('name', 'in', DEFAULT_ACCESS_ROLES)])])]

        vlist = [x.copy() for x in vlist]
        for values in vlist:
            if not values.get('code'):
                config = Configuration(1)
                values['code'] = Sequence.get_id(config.content_sequence.id)

        acls = {}
        elist = super(Content, cls).create(vlist)
        for entry in elist:
            if entry.acl:
                continue
            # always autocreate creator acl
            if entry.entity_creator and entry.entity_creator.web_user:
                acls[entry.entity_creator.web_user.id] = {
                    'entity': str(entry),
                    'web_user': entry.entity_creator.web_user.id,
                    'roles': default_roles
                }
        AccessControlEntry.create(list(acls.values()))

        return elist

    @classmethod
    def copy(cls, contents, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['code'] = None
        return super(Content, cls).copy(contents, default=default)

    def get_rec_name(self, name):
        result = '%s: %s %s %s %sHz %sBit' % (
            self.name,
            hurry.filesize.size(self.size, system=hurry.filesize.si)
            if self.size else '0M',
            (
                "{:.0f}:{:02.0f}".format(*divmod(self.length, 60))
                if self.length else '00:00'
            ),
            (
                'none' if self.channels in (None, 0) else
                'mono' if self.channels == 1 else
                'stereo' if self.channels == 2 else
                'multi'
            ),
            self.sample_rate if self.sample_rate else '0',
            self.sample_width if self.sample_width else '0',
        )
        return result

    @classmethod
    def search_rec_name(cls, name, clause):
        return [
            'OR',
            ('code',) + tuple(clause[1:]),
            ('uuid',) + tuple(clause[1:]),
            ('name',) + tuple(clause[1:]),
        ]

    def permits(self, web_user, code, derive=True):
        if super(Content, self).permits(web_user, code, derive):
            return True
        if not derive:
            return False
        derivation = {
            'view_content':   'view_artist_content',
            'edit_content':   'edit_artist_content',
            'delete_content': 'delete_artist_content',
        }
        if self.creation and self.creation.artist:
            for ace in self.creation.artist.acl:
                if ace.web_user != web_user:
                    continue
                for role in ace.roles:
                    for permission in role.permissions:
                        if permission.code == derivation[code]:
                            return True
        return False

    def permissions(self, web_user, valid_codes=False, derive=True):
        direct_permissions = super(Content, self).permissions(
            web_user, valid_codes, derive)
        if not derive:
            return direct_permissions
        derivation = {
            'view_artist_content':   'view_content',
            'edit_artist_content':   'edit_content',
            'delete_artist_content': 'delete_content',
        }
        if not set(valid_codes).intersection(set(derivation.values())):
            return direct_permissions
        permissions = set(direct_permissions)
        if self.creation and self.creation.artist:
            for ace in self.creation.artist.acl:
                if not derivation:
                    continue
                if ace.web_user != web_user:
                    continue
                for role in ace.roles:
                    for permission in role.permissions:
                        if permission.code in derivation:
                            permissions.add(derivation[permission.code])
                            del derivation[permission.code]
            if valid_codes:
                permissions = permissions.intersection(valid_codes)
        return tuple(permissions)


class Checksum(ModelSQL, ModelView):
    'Checksum'
    __name__ = 'checksum'
    _rec_name = 'code'
    _history = True
    origin = fields.Reference(
        'Origin', [
            ('content', 'Content'),
            ('harddisk', 'Harddisk'),
            ('harddisk.filesystem', 'Filesystem')
        ],
        help='The originating data of the checksum')
    code = fields.Char(
        'Checksum', required=True, help='The string of the Checksum.')
    timestamp = fields.DateTime(
        'Timestamp', states={'required': True},
        help='The point in time of the Checksum.')
    algorithm = fields.Char(
        'Algorithm', states={'required': True},
        help='The algorithm for the Checksum.')
    begin = fields.Integer(
        'Begin', help='The position of the first byte of the Checksum.')
    end = fields.Integer(
        'End', help='The position of the last byte of the Checksum.')


##############################################################################
# Portal
##############################################################################


acl_objects = [
    ('artist', 'Artist'),
    ('release', 'Release'),
    ('creation', 'Creation'),
    ('content', 'Content'),
]


class AccessControlEntry(ModelSQL, ModelView):
    'Access Control Entry'
    __name__ = 'ace'
    _history = True

    web_user = fields.Many2One(
        'web.user', 'Web User', required=True,
        help='The web user interacting with an object.')
    party = fields.Function(
        fields.Many2One(
            'party.party', 'Party',
            help='The party of the web user interacting with an object.'
        ), 'get_party')
    entity = fields.Reference(
        'Object', acl_objects, required=True,
        help='The object being interacted with.')
    roles = fields.Many2Many(
        'ace-ace.role', 'ace', 'role', 'Roles',
        states={'required': True},
        help='Individual roles of a party for an object.')
    roles_list = fields.Function(
        fields.Char('Roles'), 'on_change_with_roles_list')

    @fields.depends('roles')
    def on_change_with_roles_list(self, name=None):
        roles = "\n".join([p.name for p in self.roles])
        return roles

    @fields.depends('web_user')
    def get_party(self, name):
        return self.web_user.party.id

    @classmethod
    def __setup__(cls):
        super(AccessControlEntry, cls).__setup__()
        cls._sql_constraints += [
            ('web_user_entity_uniq', 'UNIQUE("web_user", "entity")',
                'Error!\n'
                'An ACE for the web user and entity already exists.'),
        ]


class AccessControlEntryRole(ModelSQL, ModelView):
    'Access Control Entry - Access Role'
    __name__ = 'ace-ace.role'
    _history = True

    ace = fields.Many2One(
        'ace', 'Entry', required=True, select=True, ondelete='CASCADE')
    role = fields.Many2One(
        'ace.role', 'Role', required=True, select=True, ondelete='CASCADE')
    # recursive = fields.Boolean(
    #     'Including Subobjects',  # TODO: require for artist, invisible else
    #     help="Does the role also apply to the subobjects?")


class AccessRole(ModelSQL, ModelView):
    'Access Role'
    __name__ = 'ace.role'
    _history = True

    name = fields.Char(
        'Role', required=True, help='The role of a party regarding an object.')
    description = fields.Text(
        'Description', help='The description of the role.')
    permissions = fields.Many2Many(
        'ace.role-ace.permission', 'role', 'permission', 'Permissions',
        help='Permissions of a role.')
    permissions_list = fields.Function(
        fields.Char('Permissions'), 'on_change_with_permissions_list')

    @fields.depends('permissions')
    def on_change_with_permissions_list(self, name=None):
        permissions = {}
        for permission in self.permissions:
            if permission.entity not in permissions:
                permissions[permission.entity] = []
            permissions[permission.entity].append(permission.name)
        return "\n".join([n for e in permissions for n in permissions[e]])


class AccessRolePermission(ModelSQL, ModelView):
    'Access Role - Access Permission'
    __name__ = 'ace.role-ace.permission'
    _history = True

    role = fields.Many2One(
        'ace.role', 'Role', required=True, select=True, ondelete='CASCADE')
    permission = fields.Many2One(
        'ace.permission', 'Permission',
        required=True, select=True, ondelete='CASCADE')


class AccessPermission(ModelSQL, ModelView):
    'Access Permission'
    __name__ = 'ace.permission'
    _history = True

    code = fields.Char(
        'Code', required=True, states={'readonly': True},
        help='The internal code for the permission.')
    entity = fields.Selection(
        acl_objects, 'Object', required=True, states={'readonly': True},
        help='The object to grant the permission for.')
    name = fields.Char(
        'Role', states={'readonly': True},
        help='The permission to be granted for a role.')
    description = fields.Text(
        'Description', states={'readonly': True},
        help='The description of the permission.')

    @classmethod
    def __setup__(cls):
        super(AccessPermission, cls).__setup__()
        cls._sql_constraints += [
            ('uuid_code', 'UNIQUE(code)',
                'The code of the permission must be unique.'),
        ]
