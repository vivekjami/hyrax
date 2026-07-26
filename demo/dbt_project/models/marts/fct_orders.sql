with orders as (
    select * from {{ ref('stg_orders') }}
),

payments as (
    select * from {{ ref('stg_payments') }}
),

-- One payment row per order (latest successful payment or any if none)
order_payments as (
    select
        order_id,
        max(case when is_successful then payment_method end) as payment_method,
        max(case when is_successful then amount end)         as amount_paid,
        count(*)                                             as payment_attempts,
        bool_or(is_successful)                              as payment_successful
    from payments
    group by order_id
),

final as (
    select
        o.order_id,
        o.customer_id,
        o.order_date,
        o.status,
        o.amount,
        o.region,
        o.is_completed,
        o.is_returned,
        p.payment_method,
        p.amount_paid,
        p.payment_attempts,
        p.payment_successful,
        -- Revenue only on completed + paid orders
        case
            when o.is_completed and p.payment_successful then o.amount
            else 0.00
        end                                                 as recognised_revenue
    from orders o
    left join order_payments p on o.order_id = p.order_id
)

select * from final
