with source as (
    select * from {{ ref('raw_orders') }}
),

staged as (
    select
        order_id,
        customer_id,
        cast(order_date as date)    as order_date,
        lower(status)               as status,
        cast(amount as decimal(10, 2)) as amount,
        lower(region)               as region,
        -- Derived flags
        case
            when status = 'completed' then true
            else false
        end                         as is_completed,
        case
            when status in ('returned', 'return_pending') then true
            else false
        end                         as is_returned
    from source
)

select * from staged
