
import pprint
import algosdk
from config import PoolType, PoolStatus, get_usdc_asset_id, get_stbl_asset_id

# asset decimals
ALGO_DECIMALS = 6
USDC_DECIMALS = 6
STBL_DECIMALS = 6

class Asset():

    def __init__(self, amm_client, asset_id):
        """Constructor method for :class:`Asset`
        :param amm_client: a :class:`AlgofiAMMClient` for interacting with the AMM
        :type amm_client: :class:`AlgofiAMMClient`
        :param asset_id: asset id
        :type asset_id: int
        """
        asset_info = amm_client.algod.asset_info(asset_id)
        self.asset_id = asset_id
        self.creator = asset_info['params']['creator']
        self.decimals = asset_info['params']['decimals']
        self.default_frozen = asset_info['params']['default-frozen']
        self.freeze = asset_info['params']['freeze']
        self.manager = asset_info['params']['manager']
        self.name = asset_info['params']['name']
        self.reserve = asset_info['params']['reserve']
        self.total = asset_info['params']['total']
        self.unit_name = asset_info['params']['unit-name']
        self.url = asset_info['params']['url']
    
    def __str__(self):
        """Returns a pretty string representation of the :class:`Asset` object
        :return: string representation of asset
        :rtype: str
        """
        return pprint.pformat({"asset_id": self.asset_id, "creator": self.creator, "decimals": self.decimals, "default_frozen": self.default_frozen,
                "freeze": self.freeze, "manager": self.manager, "name": self.name, "reserve": self.reserve, "total": self.total,
                "unit_name": self.unit_name, "url": self.url})

    def refresh_price(self):
        """Returns the dollar price of the asset with a simple matching algorithm
        """

        usdc_asset_id = get_usdc_asset_id(self.amm_client.network)
        stbl_asset_id = get_stbl_asset_id(self.amm_client.network)

        usdc_pool = self.amm_client.get_pool(PoolType.CONSTANT_PRODUCT_30BP_FEE, self.asset_id, usdc_asset_id)
        if (usdc_pool == PoolStatus.ACTIVE):
            self.price = usdc_pool.get_pool_price(self.asset_id) * (10**(self.decimals - USDC_DECIMALS))
            return
        
        stbl_pool = self.amm_client.get_pool(PoolType.CONSTANT_PRODUCT_30BP_FEE, self.asset_id, stbl_asset_id)
        if (stbl_pool.pool_status == PoolStatus.ACTIVE):
            self.price = (stbl_pool.get_pool_price(self.asset_id)) * (10**(self.decimals - STBL_DECIMALS))
            return
        
        algo_pool = self.amm_client.get_pool(PoolType.CONSTANT_PRODUCT_30BP_FEE, self.asset_id, ALGO_ASSET_ID)
        if (algo_pool.pool_status == PoolStatus.ACTIVE):
            price_in_algo = algo_pool.get_pool_price(self.asset_id) * (10**(self.decimals - ALGO_DECIMALS))
            usdc_algo_pool = self.amm_client.get_pool(PoolType.CONSTANT_PRODUCT_30BP_FEE, usdc_asset_id, ALGO_ASSET_ID)
            if (usdc_algo_pool.pool_status == PoolStatus.ACTIVE):
                self.price = price_in_algo * usdc_algo_pool.get_pool_price(ALGO_ASSET_ID)
                return

        # unable to find price
        self.price = 0