with customers as (
    select * from {{ ref('stg_customers') }}
),

orders as (
    select * from {{ ref('fct_orders') }}
),

customer_orders as (
    select
        customer_id,
        count(*)                                        as total_orders,
        count(*) filter (where is_completed)            as completed_orders,
        count(*) filter (where is_returned)             as returned_orders,
        sum(recognised_revenue)                         as total_revenue,
        avg(recognised_revenue)
            filter (where recognised_revenue > 0)       as avg_order_value,
        min(order_date)                                 as first_order_date,
        max(order_date)                                 as last_order_date,
        -- Approximate days since last order (freshness signal)
        datediff('day', max(order_date), current_date)  as days_since_last_order
    from orders
    group by customer_id
),

final as (
    select
        c.customer_id,
        c.name,
        c.email,
        c.country,
        c.plan,
        c.value_segment,
        c.created_at                                    as customer_since,
        coalesce(o.total_orders, 0)                     as total_orders,
        coalesce(o.completed_orders, 0)                 as completed_orders,
        coalesce(o.returned_orders, 0)                  as returned_orders,
        coalesce(o.total_revenue, 0.00)                 as lifetime_value,
        o.avg_order_value,
        o.first_order_date,
        o.last_order_date,
        o.days_since_last_order,
        -- LTV tier
        case
            when coalesce(o.total_revenue, 0) >= 1000  then 'champion'
            when coalesce(o.total_revenue, 0) >= 500   then 'loyal'
            when coalesce(o.total_revenue, 0) >= 100   then 'potential'
            else                                             'new'
        end                                             as ltv_tier
    from customers c
    left join customer_orders o on c.customer_id = o.customer_id
)

select * from final
