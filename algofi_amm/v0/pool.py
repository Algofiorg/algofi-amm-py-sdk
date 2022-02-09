
import time
import math
from algosdk.logic import get_application_address
from algosdk.future.transaction import LogicSigAccount, LogicSigTransaction, OnComplete, StateSchema, ApplicationCreateTxn, \
    ApplicationOptInTxn, ApplicationNoOpTxn, OnComplete
from .config import PoolStatus, get_validator_index, get_approval_program_by_pool_type, get_clear_state_program, get_swap_fee, get_manager_application_id
from .balance_delta import BalanceDelta
from .logic_sig_generator import generate_logic_sig
from ..contract_strings import algofi_manager_strings as manager_strings
from ..contract_strings import algofi_pool_strings as pool_strings
from ..utils import PARAMETER_SCALE_FACTOR, TransactionGroup, get_application_local_state, get_application_global_state, get_params, int_to_bytes, get_payment_txn


class Pool():

    def __init__(self, algod_client, indexer_client, historical_indexer_client, network, pool_type, asset1, asset2):
        """Constructor method for :class:`Pool`

        :param algod_client: a :class:`AlgodClient` object for interacting with the network
        :type algod_client: :class:`AlgodClient`
        :param indexer_client: a :class:`IndexerClient` object for interacting with the network
        :type indexer_client: :class:`IndexerClient`
        :param historical_indexer_client: a :class:`IndexerClient` object for interacting with the network (historical state)
        :type historical_indexer_client: :class:`IndexerClient`
        :param network: network ("testnet" or "mainnet")
        :type network: str
        :param pool_type: a :class:`PoolType` object for the type of pool (e.g. 30bp, 100bp fee)
        :type pool_type: :class:`PoolType`
        :param asset1: a :class:`Asset` representing the first asset of the pool
        :type asset1: :class:`Asset`
        :param asset2: a :class:`Asset` representing the second asset of the pool
        :type asset2: :class:`Asset`
        """

        if (asset1.asset_id >= asset2.asset_id):
            raise Exception("Invalid asset ordering. Asset 1 id must be less then asset 2 id.")
        
        self.algod = algod_client
        self.indexer = indexer_client
        self.historical_indexer = historical_indexer_client
        self.network = network
        self.pool_type = pool_type
        self.asset1 = asset1
        self.asset2 = asset2
        self.manager_application_id = get_manager_application_id(network)
        self.manager_address = get_application_address(self.manager_application_id)
        self.validator_index = get_validator_index(network, pool_type)
        self.logic_sig = LogicSigAccount(generate_logic_sig(asset1.asset_id, asset2.asset_id, self.manager_application_id, self.validator_index))
        self.swap_fee = get_swap_fee(pool_type)

        # get local state
        logic_sig_local_state = get_application_local_state(self.algod, self.logic_sig.address(), self.manager_application_id)
        if logic_sig_local_state:
            self.pool_status = PoolStatus.ACTIVE
        else:
            self.pool_status = PoolStatus.UNINITIALIZED
        
        if logic_sig_local_state:

            if (logic_sig_local_state[manager_strings.registered_asset_1_id] != asset1.asset_id) or \
            (logic_sig_local_state[manager_strings.registered_asset_2_id] != asset2.asset_id) or \
            (logic_sig_local_state[manager_strings.validator_index] != self.validator_index):
                raise Exception("Logic sig state does not match as expected")
            
            self.application_id = logic_sig_local_state[manager_strings.registered_pool_id]
            self.address = get_application_address(self.application_id)

            # get global state
            pool_state = get_application_global_state(self.algod, self.application_id)
            self.lp_asset_id = pool_state[pool_strings.lp_id]
            self.admin = pool_state[pool_strings.admin]
            self.reserve_factor = pool_state[pool_strings.reserve_factor]
            self.flash_loan_fee = pool_state[pool_strings.flash_loan_fee]
            self.max_flash_loan_ratio = pool_state[pool_strings.max_flash_loan_ratio]

        # Refresh state after initialization
        self.refresh_state()

    def refresh_metadata(self):
        """Refresh the metadata of the pool (e.g. if now initialized)
        """

        logic_sig_local_state = get_application_local_state(self.algod, self.logic_sig.address(), self.manager_application_id)
        if logic_sig_local_state:
            self.pool_status = PoolStatus.ACTIVE
        else:
            self.pool_status = PoolStatus.UNINITIALIZED
            raise Exception("Pool is not created or uninitialized")
        
        if (logic_sig_local_state[manager_strings.registered_asset_1_id] != self.asset1.asset_id) or \
           (logic_sig_local_state[manager_strings.registered_asset_2_id] != self.asset2.asset_id) or \
           (logic_sig_local_state[manager_strings.validator_index] != self.validator_index):
           raise Exception("Logic sig state does not match as expected")
        
        self.application_id = logic_sig_local_state[manager_strings.registered_pool_id]
        self.address = get_application_address(self.application_id)

        # get global state
        pool_state = get_application_global_state(self.algod, self.application_id)
        self.lp_asset_id = pool_state[pool_strings.lp_id]
        self.admin = pool_state[pool_strings.admin]
        self.reserve_factor = pool_state[pool_strings.reserve_factor]
        self.flash_loan_fee = pool_state[pool_strings.flash_loan_fee]
        self.max_flash_loan_ratio = pool_state[pool_strings.max_flash_loan_ratio]

    def refresh_state(self):
        """Refresh the global state of the pool
        """

        # load pool state
        pool_state = get_application_global_state(self.algod, self.application_id)
        self.asset1_balance = pool_state[pool_strings.balance_1]
        self.asset2_balance = pool_state[pool_strings.balance_2]
        self.lp_circulation = pool_state[pool_strings.lp_circulation]
        self.asset1_reserve = pool_state[pool_strings.asset1_reserve]
        self.asset2_reserve = pool_state[pool_strings.asset2_reserve]
        self.latest_time = pool_state[pool_strings.latest_time]
        self.cumsum_time_weighted_asset1_to_asset2_price = pool_state[pool_strings.cumsum_time_weighted_asset1_to_asset2_price]
        self.cumsum_time_weighted_asset2_to_asset1_price = pool_state[pool_strings.cumsum_time_weighted_asset2_to_asset1_price]
        self.cumsum_volume_asset1 = pool_state[pool_strings.cumsum_volume_asset1]
        self.cumsum_volume_asset2 = pool_state[pool_strings.cumsum_volume_asset2]
        self.cumsum_volume_weighted_asset1_to_asset2_price = pool_state[pool_strings.cumsum_volume_weighted_asset1_to_asset2_price]
        self.cumsum_volume_weighted_asset2_to_asset1_price = pool_state[pool_strings.cumsum_volume_weighted_asset2_to_asset1_price]
        self.cumsum_fees_asset1 = pool_state[pool_strings.cumsum_fees_asset1]
        self.cumsum_fees_asset2 = pool_state[pool_strings.cumsum_fees_asset2]

    def get_pool_price(self, asset_id):
        """Gets the price of the pool in terms of the asset with given asset_id

        :param asset_id: asset id of the asset to price
        :type asset_id: int
        :return: price of pool in terms of asset with given asset_id
        :rtype: float
        """

        if (asset_id == self.asset1.asset_id):
            return self.asset1_balance / self.asset2_balance
        elif (asset_id == self.asset2.asset_id):
            return self.asset2_balance / self.asset1_balance
        else:
            raise Exception("Invalid asset id")
    
    def sign_txn_with_logic_sig(self, transaction):
        """Returns input transaction signed with logic sig of pool

        :param transaction: a :class:`Transaction` to sign
        :type transaction: :class:`Transaction`
        :return: transaction signed with logic sig of pool
        :rtype: :class:`SignedTransaction`
        """

        return LogicSigTransaction(transaction, self.logic_sig)
    
    def get_create_pool_txn(self, sender):
        """Returns unsigned CreatePool transaction with given sender

        :param sender: sender
        :type sender: str
        :return: unsigned CreatePool transaction with given sender
        :rtype: :class:`ApplicationCreateTxn`
        """

        if (self.pool_status == PoolStatus.ACTIVE):
            raise Exception("Pool already created and active - cannot generate create pool txn")
        
        params = get_params(self.algod)

        approval_program = get_approval_program_by_pool_type(self.pool_type)
        clear_state_program = get_clear_state_program()

        global_schema = StateSchema(num_uints=60, num_byte_slices=4)
        local_schema = StateSchema(num_uints=0, num_byte_slices=0)

        txn0 = ApplicationCreateTxn(
            sender=sender,
            sp=params,
            on_complete=OnComplete.NoOpOC,
            approval_program=approval_program,
            clear_program=clear_state_program,
            global_schema=global_schema,
            local_schema=local_schema,
            app_args=[int_to_bytes(self.asset1.asset_id), int_to_bytes(self.asset2.asset_id)],
            foreign_apps=[self.manager_application_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big'),
            extra_pages=3
        )

        return TransactionGroup([txn0])
    
    def get_initialize_pool_txns(self, sender, pool_app_id):
        """Get group transaction for initializing the pool. First, the manager is 
        funded (which funds the pool contract (for opting into assets, creating LP token)
        via an inner payment txn. Then, the logic sig is funded to opt into manager.
        After, the sender calls the initialize function on the pool. This transaction
        "registers" the pool with the manager so it is searchable via SDK.

        :param sender: sender
        :type sender: str
        :param pool_app_id: application id of the pool to initialize
        :type pool_app_id: int
        :return: unsigned group transaction :class:`TransactionGroup` with sender for initializing pool
        :rtype: :class:`TransactionGroup`
        """

        if (self.pool_status == PoolStatus.ACTIVE):
            raise Exception("Pool already active - cannot generate initialize pool txn")
        
        params = get_params(self.algod)

        # fund manager
        txn0 = get_payment_txn(params, sender, self.manager_address, amount=500000)

        # fund logic sig
        txn1 = get_payment_txn(params, sender, self.logic_sig.address(), amount=835000)

        # opt logic sig into manager
        params.fee = 2000
        txn2 = ApplicationOptInTxn(
            sender=self.logic_sig.address(),
            sp=params,
            index=self.manager_application_id,
            app_args=[int_to_bytes(self.asset1.asset_id), int_to_bytes(self.asset2.asset_id), int_to_bytes(self.validator_index)],
            accounts=[get_application_address(pool_app_id)],
            foreign_apps=[pool_app_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        # call pool initialize fcn
        params.fee = 4000
        foreign_assets = [self.asset2.asset_id] if self.asset1.asset_id == 1 else [self.asset1.asset_id, self.asset2.asset_id]
        txn3 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=pool_app_id,
            app_args=[bytes(pool_strings.initialize_pool, "utf-8")],
            foreign_apps=[self.manager_application_id],
            foreign_assets=foreign_assets,
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        return TransactionGroup([txn0, txn1, txn2, txn3])

    def get_lp_token_opt_in_txn(self, sender):
        """Get lp token opt in transaction for the given sender

        :param sender: sender
        :type sender: str
        :return: lp token opt in transaction for the given sender
        :rtype: :class:`PaymentTxn` or :class:`AssetTransferTxn`
        """

        params = get_params(self.algod)
        return get_payment_txn(params, sender, sender, amount=int(0), asset_id=self.lp_asset_id)
    
    def get_pool_txns(self, sender, asset1_amount, asset2_amount, maximum_slippage):
        """Get group transaction for pooling with given asset amounts and maximum slippage.
        The two assets are sent via two :class:`PaymentTxn` / :class:`AssetTransferTxn`. Then, a pool call
        is made from which the LP tokens are issued via inner asset transfer txn. Lastly,
        two redeem residual calls are made to redeem residuals of assets 1 and 2 that
        are not used in the pooling operation. The ratio of the incoming asset amounts
        is compared to the ratio on the smart contract. If it differs (up or down) by more
        than the max_slippage percent, the transaction fails.

        :param sender: sender
        :type sender: str
        :param asset1_amount: asset amount for the first asset
        :type asset1_amount: int
        :param asset2_amount: asset amount for the second asset
        :type asset2_amount: int
        :param maximum_slippage: maximum slippage percent (scaled by 1000000) allowed
        :type maximum_slippage: int
        :return: group transaction for pooling with given asset amounts and maximum slippage
        :rtype: :class:`TransactionGroup`
        """

        params = get_params(self.algod)

        # send asset 1
        txn0 = get_payment_txn(params, sender, self.address, asset1_amount, self.asset1.asset_id)

        # send asset 2
        txn1 = get_payment_txn(params, sender, self.address, asset2_amount, self.asset2.asset_id)

        # pool
        params.fee = 3000
        txn2 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.pool, "utf-8"), int_to_bytes(maximum_slippage)],
            foreign_apps=[self.manager_application_id],
            foreign_assets=[self.lp_asset_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        # redeem asset 1 residual
        params.fee = 1000
        txn3 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.redeem_pool_asset1_residual, "utf-8")],
            foreign_assets=[self.asset1.asset_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        # redeem asset 2 residual
        txn4 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.redeem_pool_asset2_residual, "utf-8")],
            foreign_assets=[self.asset2.asset_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        return TransactionGroup([txn0, txn1, txn2, txn3, txn4])
    
    def get_burn_txns(self, sender, burn_amount):
        """Get group transaction for burn with given burn amount. The LP token
        is transferred via :class:`AssetTransferTxn`. Then, two burn calls are made,
        one for each asset.

        :param sender: sender
        :type sender: str
        :param burn_amount: lp asset amount to burn
        :type burn_amount: int
        :return: group transaction for burn with given burn amount
        :rtype: :class:`TransactionGroup`
        """

        params = get_params(self.algod)

        # send lp token
        txn0 = get_payment_txn(params, sender, self.address, burn_amount, self.lp_asset_id)

        # burn asset 1 out
        params.fee = 2000
        txn1 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.burn_asset1_out, "utf-8")],
            foreign_assets=[self.asset1.asset_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        # burn asset 2 out
        txn2 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.burn_asset2_out, "utf-8")],
            foreign_assets=[self.asset2.asset_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        return TransactionGroup([txn0, txn1, txn2])
    
    def get_swap_exact_for_txns(self, sender, swap_in_asset, swap_in_amount, min_amount_to_receive):
        """Get group transaction for swap exact for transaction. An exact amount of the asset
        to be swapped is sent via a :class:`PaymentTxn` or :class:`AssetTransferTxn`. 
        Then, a swap exact for call is made from which the output asset is sent via inner transaction.
        If the output asset amount exceeds the min_amount_to_receive, the transaction succeeds.

        :param sender: sender
        :type sender: str
        :param swap_in_asset: asset to swap
        :type swap_in_asset: :class:`Asset`
        :param swap_in_amount: asset amount of incoming asset
        :type swap_in_amount: int
        :param min_amount_to_receive: minimum amount of outgoing asset to receive, assert failure if not
        :type min_amount_to_receive: int
        :return: group transaction for swap exact for transaction
        :rtype: :class:`TransactionGroup`
        """

        params = get_params(self.algod)

        # send swap in asset
        txn0 = get_payment_txn(params, sender, self.address, swap_in_amount, swap_in_asset.asset_id)

        # swap exact for
        params.fee = 2000
        foreign_assets = [self.asset2.asset_id] if swap_in_asset.asset_id == self.asset1.asset_id else ([self.asset1.asset_id] if self.asset1.asset_id != 1 else [])
        txn1 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.swap_exact_for, "utf-8"), int_to_bytes(min_amount_to_receive)],
            foreign_apps=[self.manager_application_id],
            foreign_assets=foreign_assets,
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        return TransactionGroup([txn0, txn1])

    def get_swap_for_exact_txns(self, sender, swap_in_asset, swap_in_amount, amount_to_receive):
        """Get group transaction for swap for exact transaction. An amount of the asset to be
        swapped is sent via a :class:`PaymentTxn` or :class:`AssetTransferTxn`. Then, swap for exact
        call is made to swap for an exact amount of the output asset. If a sufficient amount
        of the incoming asset has been sent, the transaction succeeds. If it succeeds, a 
        residual amount of the incoming asset is redeemed by the user in the next call.

        :param sender: sender
        :type sender: str
        :param swap_in_asset: asset to swap
        :type swap_in_asset: :class:`Asset`
        :param swap_in_amount: asset amount of incoming asset
        :type swap_in_amount: int
        :param amount_to_receive: exact amount to receive of outgoing asset, assert fail if not possible
        :type amount_to_receive: int
        :return: group transaction for swap for exact transaction
        :rtype: :class:`TransactionGroup`
        """

        params = get_params(self.algod)

        # send swap in asset
        txn0 = get_payment_txn(params, sender, self.address, swap_in_amount, swap_in_asset.asset_id)

        # swap for exact
        params.fee = 2000
        foreign_assets = [self.asset2.asset_id] if swap_in_asset.asset_id == self.asset1.asset_id else ([self.asset1.asset_id] if self.asset1.asset_id != 1 else [])
        txn1 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.swap_for_exact, "utf-8"), int_to_bytes(amount_to_receive)],
            foreign_apps=[self.manager_application_id],
            foreign_assets=foreign_assets,
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        # redeem unused swap in asset
        params.fee = 2000
        txn2 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[bytes(pool_strings.redeem_swap_residual, "utf-8")],
            foreign_apps=[self.manager_application_id],
            foreign_assets=[swap_in_asset.asset_id],
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        return TransactionGroup([txn0, txn1, txn2])
    
    def get_flash_loan_txns(self, sender, flash_loan_asset, flash_loan_amount, group_transaction):
        """Get group transaction for swap exact for transaction

        :param sender: sender
        :type sender: str
        :param flash_loan_asset: asset to borrow in flash loan
        :type flash_loan_asset: :class:`Asset`
        :param flash_loan_amount: asset amount to borrow
        :type flash_loan_amount: int
        :param group_transaction: a group transaction to "sandwich" between flash loan transaction
        :type group_transaction: :class:`TransactionGroup`
        :return: group transaction for flash loan transaction composed with group transaction
        :rtype: :class:`TransactionGroup`
        """

        params = get_params(self.algod)

        # flash loan txn
        params.fee = 2000
        foreign_assets = [self.asset2.asset_id] if flash_loan_asset.asset_id == self.asset2.asset_id else ([self.asset1.asset_id] if self.asset1.asset_id != 1 else [])
        txn0 = ApplicationNoOpTxn(
            sender=sender,
            sp=params,
            index=self.application_id,
            app_args=[
                bytes(pool_strings.flash_loan, "utf-8"),
                int_to_bytes(flash_loan_asset.asset_id),
                int_to_bytes(flash_loan_amount)
            ],
            foreign_apps=[self.manager_application_id],
            foreign_assets=foreign_assets,
            note=int(time.time() * 1000 * 1000).to_bytes(8, 'big')
        )

        # repayment txn
        params.fee = 1000
        flash_loan_fee = (flash_loan_amount * self.flash_loan_fee) // PARAMETER_SCALE_FACTOR + int(1)
        repay_amount = flash_loan_amount + flash_loan_fee
        txn1 = get_payment_txn(params, sender, self.address, repay_amount, flash_loan_asset.asset_id)

        # set group to None
        transactions = [txn0] + group_transaction.transactions + [txn1]
        for i in range(len(transactions)):
            transactions[i].group = None

        return TransactionGroup([txn0] + group_transaction.transactions + [txn1])

    def get_empty_pool_quote(self, asset1_pooled_amount, asset2_pooled_amount):
        """Get pool quote for an empty pool

        :param asset1_pooled_amount: asset 1 pooled amount
        :type asset1_pooled_amount: int
        :param asset2_pooled_amount: asset 2 pooled amount
        :type asset2_pooled_amount: int
        :return: pool quote for an empty pool
        :rtype: :class:`BalanceDelta`
        """

        if (asset1_pooled_amount * asset2_pooled_amount > 2**64-1):
            lps_issued = int((asset1_pooled_amount)**(0.5)) * int((asset2_pooled_amount)**(0.5))
        else:
            lps_issued = int((asset1_pooled_amount * asset2_pooled_amount)**(0.5))
        
        return BalanceDelta(self, -1 * asset1_pooled_amount, -1*asset2_pooled_amount, lps_issued)
    
    def get_pool_quote(self, asset_id, asset_amount):
        """Get full pool quote for a given asset id and amount

        :param asset_id: asset id of the asset to pool
        :type asset_id: int
        :param asset_amount: asset amount of the asset to pool
        :type asset_amount: int
        :return: pool quote for a non-empty pool
        :rtype: :class:`BalanceDelta`
        """

        if (self.lp_circulation == 0):
            raise Exception("Error: pool is empty")
        
        if (asset_id == self.asset1.asset_id):
            asset1_pooled_amount = asset_amount
            asset2_pooled_amount = int(asset1_pooled_amount * self.asset2_balance / self.asset1_balance)
        else:
            asset2_pooled_amount = asset_amount
            asset1_pooled_amount = int(asset2_pooled_amount * self.asset1_balance / self.asset2_balance)
        
        lps_issued = int(asset1_pooled_amount * self.lp_circulation / self.asset1_balance)

        return BalanceDelta(self, -1 * asset1_pooled_amount, -1 * asset2_pooled_amount, lps_issued)
    
    def get_burn_quote(self, lp_amount):
        """Get burn quote for a given amount of lps to burn

        :param lp_amount: lp amount to burn
        :type lp_amount: int
        :return: burn quote for a given amount of lps to burn
        :rtype: :class:`BalanceDelta`
        """

        if (self.lp_circulation == 0):
            raise Exception("Error: pool is empty")
        
        if (self.lp_circulation < lp_amount):
            raise Exception("Error: cannot burn more lp tokens than are in circulation")
        
        asset1_amount = int(lp_amount * self.asset1_balance / self.lp_circulation)
        asset2_amount = int(lp_amount * self.asset2_balance / self.lp_circulation)

        return BalanceDelta(self, asset1_amount, asset2_amount, -1 * lp_amount)
    
    def get_swap_exact_for_quote(self, swap_in_asset_id, swap_in_amount):
        """Get swap exact for quote for a given asset id and swap amount

        :param swap_in_asset_id: id of incoming asset to swap
        :type swap_in_asset_id: int
        :param swap_in_amount: amount of incoming asset to swap
        :type swap_in_amount: int
        :return: swap exact for quote for a given asset id and swap amount
        :rtype: :class:`BalanceDelta`
        """

        if (self.lp_circulation == 0):
            raise Exception("Error: pool is empty")
        
        swap_in_amount_less_fees = swap_in_amount - int(math.ceil(swap_in_amount * self.swap_fee))

        if (swap_in_asset_id == self.asset1.asset_id):
            swap_out_amount = int((self.asset2_balance * swap_in_amount_less_fees) / (self.asset1_balance + swap_in_amount_less_fees))
        else:
            swap_out_amount = int((self.asset1_balance * swap_in_amount_less_fees) / (self.asset2_balance + swap_in_amount_less_fees))
        
        return BalanceDelta(self, swap_out_amount, -1 * swap_in_amount, 0)

    def get_swap_for_exact_quote(self, swap_out_asset_id, swap_out_amount):
        """Get swap for exact quote for a given asset id and swap amount

        :param swap_out_asset_id: id of outgoing asset
        :type swap_out_asset_id: int
        :param swap_out_amount: amount of outgoing asset
        :type swap_out_amount: int
        :return: swap for exact quote for a given outgoing asset id and amount
        :rtype: :class:`BalanceDelta`
        """

        if (self.lp_circulation == 0):
            raise Exception("Error: pool is empty")
        
        if (swap_out_asset_id == self.asset1.asset_id):
            swap_in_amount_less_fees = int((self.asset2_balance * swap_out_amount) / (self.asset1_balance - swap_out_amount)) - 1
        else:
            swap_in_amount_less_fees = int((self.asset1_balance * swap_out_amount) / (self.asset2_balance - swap_out_amount)) - 1
        
        swap_in_amount = math.ceil(swap_in_amount_less_fees / (1 - self.swap_fee))

        if (swap_out_asset_id == self.asset1.asset_id):
            return BalanceDelta(self, swap_out_amount, -1 * swap_in_amount, 0)
        else:
            return BalanceDelta(self, -1 * swap_in_amount, swap_out_amount, 0)